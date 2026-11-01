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
    Company,
    CompanyApiKey,
    CompanyPublic,
    ExportBatch,
    ExportQueueItem,
    ExportQueuePublic,
    ExportResult,
    Invoice,
    InvoiceState,
    Message,
    as_vat_items,
)
from app.services import company_access, storage
from app.services import invoices as invoice_service

router = APIRouter(prefix="/export", tags=["export"])

GlobalOrCompany = UUID | None  # None = global key, UUID = scoped to that company


def require_export_access(
    session: SessionDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> GlobalOrCompany:
    """Global EXPORT_API_KEY sees everything; a company key sees its company."""
    global_enabled = bool(settings.EXPORT_API_KEY)
    has_company_keys = (
        session.exec(
            select(CompanyApiKey.id).where(col(CompanyApiKey.revoked_at).is_(None))
        ).first()
        is not None
    )
    if not global_enabled and not has_company_keys:
        raise HTTPException(
            status_code=503,
            detail="Export API je vypnuto (EXPORT_API_KEY ani klíče firem nenastaveny)",
        )
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Neplatný API klíč")
    if global_enabled and secrets.compare_digest(x_api_key, settings.EXPORT_API_KEY):
        return None
    key = company_access.resolve_api_key(session, x_api_key)
    if key is None:
        raise HTTPException(status_code=401, detail="Neplatný API klíč")
    return key.company_id


def _assert_company_scope(
    company_id: GlobalOrCompany, invoice_company_id: UUID | None
) -> None:
    if company_id is not None and invoice_company_id != company_id:
        raise HTTPException(status_code=404, detail="Faktura nenalezena")


router = APIRouter(prefix="/export", tags=["export"])


def _exportable_invoices(
    session: Session, company_id: GlobalOrCompany = None
) -> list[Invoice]:
    query = (
        select(Invoice)
        .where(Invoice.state == InvoiceState.READY_FOR_EXPORT.value)
        .where(col(Invoice.company_id).is_not(None))
        .where(col(Invoice.approved_at).is_not(None))
        .order_by(col(Invoice.approved_at).asc())
    )
    if company_id is not None:
        query = query.where(col(Invoice.company_id) == company_id)
    return list(session.exec(query).all())


@router.get("/queue", response_model=ExportQueuePublic)
def get_export_queue(
    session: SessionDep,
    scope: Annotated[GlobalOrCompany, Depends(require_export_access)],
) -> ExportQueuePublic:
    """Approved invoices ready to be imported into Pohoda."""
    items: list[ExportQueueItem] = []
    for invoice in _exportable_invoices(session, scope):
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
def get_export_file(
    session: SessionDep,
    invoice_id: UUID,
    scope: Annotated[GlobalOrCompany, Depends(require_export_access)],
) -> Response:
    invoice = session.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Faktura nenalezena")
    _assert_company_scope(scope, invoice.company_id)
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
def report_export_result(
    session: SessionDep,
    result: ExportResult,
    scope: Annotated[GlobalOrCompany, Depends(require_export_access)],
) -> Message:
    """Connector reports the import outcome for one invoice (idempotent)."""
    invoice = session.get(Invoice, result.invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Faktura nenalezena")
    _assert_company_scope(scope, invoice.company_id)

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
def sync_companies() -> Message:
    """Removed: companies are the app's own recipients, Pohoda no longer sends them."""
    raise HTTPException(
        status_code=410,
        detail="Sync firem byl zrušen – firmy (příjemce dokladů) spravujte v aplikaci",
    )
