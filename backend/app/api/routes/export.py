"""Batch export API for the Pohoda connector application.

Authenticated by the `X-API-Key` header (`EXPORT_API_KEY` setting), not by
user JWTs. The connector pulls the queue, imports invoices into Pohoda
(via the file attachment) and reports the result back per invoice.
"""

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlmodel import Session, col, select

from app.api.deps import SessionDep
from app.core.config import settings
from app.models import (
    AuditLog,
    Company,
    CompanyPublic,
    CompanySyncPayload,
    ExportBatch,
    ExportQueueItem,
    ExportQueuePublic,
    ExportResult,
    Invoice,
    InvoiceState,
    Message,
    as_vat_items,
    get_datetime_utc,
)
from app.services import companies as company_service
from app.services import invoices as invoice_service
from app.services import storage

router = APIRouter(prefix="/export", tags=["export"])


def verify_export_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.EXPORT_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Export API je vypnuto (EXPORT_API_KEY není nastaveno)",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.EXPORT_API_KEY):
        raise HTTPException(status_code=401, detail="Neplatný API klíč")


router = APIRouter(
    prefix="/export",
    tags=["export"],
    dependencies=[Depends(verify_export_api_key)],
)


def _exportable_invoices(session: Session) -> list[Invoice]:
    return list(
        session.exec(
            select(Invoice)
            .where(Invoice.state == InvoiceState.READY_FOR_EXPORT.value)
            .where(col(Invoice.company_id).is_not(None))
            .where(col(Invoice.approved_at).is_not(None))
            .order_by(col(Invoice.approved_at).asc())
        ).all()
    )


@router.get("/queue", response_model=ExportQueuePublic)
def get_export_queue(session: SessionDep) -> ExportQueuePublic:
    """Approved invoices ready to be imported into Pohoda."""
    items: list[ExportQueueItem] = []
    for invoice in _exportable_invoices(session):
        company = (
            session.get(Company, invoice.company_id) if invoice.company_id else None
        )
        if company is None or not company.is_active:
            continue
        items.append(
            ExportQueueItem(
                id=invoice.id,
                state=invoice.state,
                original_filename=invoice.original_filename,
                content_type=invoice.content_type,
                export_url=f"{settings.API_V1_STR}/export/invoices/{invoice.id}/file",
                company=CompanyPublic.model_validate(company),
                invoice_number=invoice.invoice_number,
                document_type=invoice.document_type,
                issue_date=invoice.issue_date,
                due_date=invoice.due_date,
                total_price=invoice.total_price,
                total_vat=invoice.total_vat,
                total_without_vat=invoice.total_without_vat,
                currency=invoice.currency,
                vat_items=as_vat_items(invoice.vat_items),
                approved_at=invoice.approved_at,
            )
        )
    return ExportQueuePublic(data=items, count=len(items))


@router.get("/invoices/{invoice_id}/file")
def get_export_file(session: SessionDep, invoice_id: UUID) -> Response:
    invoice = session.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Faktura nenalezena")
    if invoice.state not in (
        InvoiceState.READY_FOR_EXPORT.value,
        InvoiceState.EXPORTED.value,
    ):
        raise HTTPException(status_code=409, detail="Faktura není ve frontě k exportu")
    try:
        data = storage.get_storage().open(invoice.storage_key)
    except FileNotFoundError, OSError:
        raise HTTPException(status_code=404, detail="Soubor nenalezen")
    return Response(content=data, media_type=invoice.content_type)


@router.post("/result", response_model=Message)
def report_export_result(session: SessionDep, result: ExportResult) -> Message:
    """Connector reports the import outcome for one invoice (idempotent)."""
    invoice = session.get(Invoice, result.invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Faktura nenalezena")

    state = InvoiceState(invoice.state)
    if result.success:
        if state is InvoiceState.EXPORTED:
            return Message(message="Již importováno")  # idempotent retry
        if state not in (
            InvoiceState.READY_FOR_EXPORT,
            InvoiceState.EXPORT_FAILED,
        ):
            raise HTTPException(
                status_code=409,
                detail="Faktura není ve frontě k exportu (chybí firma nebo schválení)",
            )
        company = (
            session.get(Company, invoice.company_id) if invoice.company_id else None
        )
        if company is None:
            raise HTTPException(
                status_code=409, detail="Faktura nemá přiřazenou firmu v databázi"
            )
        if result.batch_ref:
            _ensure_batch(session, result.batch_ref)
        invoice_service.record_export_result(
            session, invoice, success=True, batch_ref=result.batch_ref
        )
    else:
        if state is InvoiceState.EXPORTED:
            raise HTTPException(
                status_code=409,
                detail="Faktura je již importována – nelze značit chybu",
            )
        invoice_service.record_export_result(
            session,
            invoice,
            success=False,
            batch_ref=result.batch_ref,
            error=result.error,
        )
    session.commit()
    return Message(message="Výsledek exportu zaznamenán")


def _ensure_batch(session: Session, batch_ref: str) -> ExportBatch:
    batch = session.exec(
        select(ExportBatch).where(ExportBatch.batch_ref == batch_ref)
    ).first()
    if batch is None:
        batch = ExportBatch(batch_ref=batch_ref)
        session.add(batch)
        session.commit()
        session.refresh(batch)
    return batch


@router.put("/companies/sync", response_model=Message)
def sync_companies(session: SessionDep, payload: CompanySyncPayload) -> Message:
    """Bulk upsert of partners from the Pohoda side (matched by IČO)."""
    created = updated = unchanged = 0
    for item in payload.companies:
        existing = session.exec(select(Company).where(Company.ico == item.ico)).first()
        if existing is None:
            company = Company(
                name=item.name,
                ico=item.ico,
                dic=item.dic,
                address=item.address,
                bank_account=item.bank_account,
                note=item.note,
                is_active=item.is_active,
                source="pohoda_sync",
            )
            session.add(company)
            session.flush()
            company_service.apply_bank_accounts(session, company, item.bank_accounts)
            created += 1
            continue
        changed = False
        if item.bank_accounts is not None:
            company_service.apply_bank_accounts(session, existing, item.bank_accounts)
            changed = True
        for field in ("name", "dic", "address", "bank_account", "note", "is_active"):
            new_value = getattr(item, field)
            if new_value is not None and new_value != getattr(existing, field):
                setattr(existing, field, new_value)
                changed = True
        existing.source = "pohoda_sync"
        if changed:
            existing.updated_at = get_datetime_utc()
            updated += 1
        else:
            unchanged += 1
        session.add(existing)
    session.commit()
    session.add(
        AuditLog(
            action="companies_synced",
            details={"created": created, "updated": updated, "unchanged": unchanged},
        )
    )
    session.commit()
    return Message(
        message=f"Sync firem: {created} nových, {updated} změněných, {unchanged} beze změny"
    )
