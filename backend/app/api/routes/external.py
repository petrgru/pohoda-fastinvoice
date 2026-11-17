"""Machine-to-machine mutations for an external accounting application.

Authenticated by the company ``X-API-Key`` header (created in the company
management dialog). Reading lives on the read-only `/data` API; this router
adds what the accounting application must write back: corrections of the
invoice before the confirmation and the processing outcome – ``exported``
carries the document number assigned by the external system. An invoice
already imported (``exported``) is locked: it cannot be edited or deleted
here or anywhere else.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session

from app.api.deps import SessionDep
from app.models import (
    ExternalStatusUpdate,
    Invoice,
    InvoicePublic,
    InvoiceState,
    InvoiceUpdate,
    RecipientApiKey,
    RecipientCompany,
    get_datetime_utc,
)
from app.services import invoices as invoice_service
from app.services import recipient_access

router = APIRouter(prefix="/external", tags=["external"])

ExternalAccess = tuple[RecipientApiKey, RecipientCompany]


def require_company_api_key(
    session: SessionDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> ExternalAccess:
    """Resolve the X-API-Key header to its (active) company key and company."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Chybí X-API-Key hlavička")
    key = recipient_access.resolve_api_key(session, x_api_key)
    if key is None:
        raise HTTPException(status_code=401, detail="Neplatný API klíč")
    company = session.get(RecipientCompany, key.recipient_company_id)
    if company is None or not company.is_active:
        raise HTTPException(status_code=403, detail="Firma je neaktivní")
    return key, company


def _get_scoped_invoice(
    session: Session, company: RecipientCompany, invoice_id: UUID
) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None or invoice.recipient_company_id != company.id:
        raise HTTPException(status_code=404, detail="Faktura nenalezena")
    return invoice


def _audit(
    session: Session,
    action: str,
    invoice: Invoice,
    key: RecipientApiKey,
    details: dict[str, object],
) -> None:
    invoice_service.log_audit(
        session,
        action,
        invoice=invoice,
        user=None,
        details={**details, "api_key": key.label},
    )


@router.patch("/invoices/{invoice_id}", response_model=InvoicePublic)
def update_invoice(
    invoice_id: UUID,
    invoice_in: InvoiceUpdate,
    session: SessionDep,
    access: Annotated[ExternalAccess, Depends(require_company_api_key)],
) -> InvoicePublic:
    """Correct a processed invoice (dates, amounts, VAT lines…).

    The recipient company and the bank accounts are out of reach of the
    external application – reassignment and fraud confirmation belong to
    the human user.
    """
    key, company = access
    invoice = _get_scoped_invoice(session, company, invoice_id)
    state = InvoiceState(invoice.state)
    if state in (InvoiceState.UPLOADED, InvoiceState.PARSING):
        raise HTTPException(status_code=409, detail="Faktura ještě není zpracovaná")
    if not invoice.can_delete:
        raise HTTPException(
            status_code=403, detail="Faktura je po importu uzamčená (nelze měnit)"
        )
    changes = invoice_in.model_dump(
        exclude_unset=True, exclude={"confirm_account_change"}
    )
    changes.pop("recipient_company_id", None)
    for field in (
        "supplier_bank_account",
        "supplier_iban",
        "supplier_bank_accounts",
    ):
        changes.pop(field, None)
    for field, value in changes.items():
        setattr(invoice, field, value)
    invoice_service.recompute_derived(invoice)
    missing = invoice_service.missing_required_dates(invoice)
    if missing and changes:
        raise HTTPException(
            status_code=422,
            detail="Chybí povinná data: " + ", ".join(missing),
        )
    invoice.updated_at = get_datetime_utc()
    session.add(invoice)
    _audit(session, "api_fields_updated", invoice, key, {"fields": sorted(changes)})
    session.commit()
    session.refresh(invoice)
    invoice_service.update_search_vector(session, invoice)
    return invoice_service.to_public(session, invoice)


@router.post("/invoices/{invoice_id}/status", response_model=InvoicePublic)
def change_status(
    invoice_id: UUID,
    status_in: ExternalStatusUpdate,
    session: SessionDep,
    access: Annotated[ExternalAccess, Depends(require_company_api_key)],
) -> InvoicePublic:
    """Report the processing outcome (see the module docstring)."""
    key, company = access
    invoice = _get_scoped_invoice(session, company, invoice_id)
    current = InvoiceState(invoice.state)
    target = InvoiceState(status_in.state)

    if target is InvoiceState.EXPORTED:
        if current is InvoiceState.EXPORTED:
            if status_in.document_number and (
                invoice.external_number != status_in.document_number
            ):
                invoice.external_number = status_in.document_number
                session.add(invoice)
                _audit(
                    session,
                    "api_status_change",
                    invoice,
                    key,
                    {"document_number": status_in.document_number},
                )
                session.commit()
                session.refresh(invoice)
            return invoice_service.to_public(session, invoice)
        if not status_in.document_number:
            raise HTTPException(
                status_code=422,
                detail="Vyplň document_number – číslo dokladu z vašeho systému",
            )
        if current not in (InvoiceState.READY_FOR_EXPORT, InvoiceState.EXPORT_FAILED):
            raise HTTPException(
                status_code=409,
                detail="Faktura není schválená k exportu (potřebuje stav ready_for_export)",
            )
        invoice.external_number = status_in.document_number
        invoice_service.record_export_result(session, invoice, success=True)
    elif target is InvoiceState.EXPORT_FAILED:
        if current is InvoiceState.EXPORTED:
            raise HTTPException(
                status_code=409,
                detail="Faktura je již importovaná – nelze značit chybu",
            )
        if current in (InvoiceState.UPLOADED, InvoiceState.PARSING):
            raise HTTPException(status_code=409, detail="Faktura ještě není zpracovaná")
        invoice_service.record_export_result(
            session, invoice, success=False, error=status_in.note or "processing failed"
        )
    else:  # re-queue a failed invoice after its correction
        if current is not InvoiceState.EXPORT_FAILED:
            raise HTTPException(
                status_code=409,
                detail="Do fronty lze zařadit pouze chybnou fakturu (export_failed)",
            )
        invoice_service.change_state(
            session, invoice, InvoiceState.READY_FOR_EXPORT, note=status_in.note
        )

    _audit(
        session,
        "api_status_change",
        invoice,
        key,
        {
            "from": current.value,
            "to": target.value,
            "document_number": status_in.document_number,
        },
    )
    session.commit()
    session.refresh(invoice)
    return invoice_service.to_public(session, invoice)
