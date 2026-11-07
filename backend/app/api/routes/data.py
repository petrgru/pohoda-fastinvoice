"""Read-only Data API for external integrations.

Authenticated by the same `X-API-Key` header as the Export API: the global
`EXPORT_API_KEY` sees every invoice, a per-company API key only the invoices
assigned to its recipient company. Nothing here ever mutates data; invoice
files are served for download as stored.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlmodel import Session, col, or_, select

from app.api.deps import SessionDep
from app.api.routes.export import GlobalOrCompany, require_export_access
from app.models import (
    DataInvoicePublic,
    DataInvoicesPublic,
    Invoice,
    InvoiceState,
    RecipientCompany,
    RecipientCompanyPublic,
)
from app.services import storage

router = APIRouter(prefix="/data", tags=["data"])


def _scoped_invoice(
    session: Session, scope: GlobalOrCompany, invoice_id: UUID
) -> Invoice:
    """Invoice visible under the key's scope; out-of-scope looks like 404."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None or (scope is not None and invoice.recipient_company_id != scope):
        raise HTTPException(status_code=404, detail="Faktura nenalezena")
    return invoice


def _to_public(session: Session, invoice: Invoice) -> DataInvoicePublic:
    public = DataInvoicePublic.model_validate(invoice, from_attributes=True)
    if invoice.recipient_company_id:
        company = session.get(RecipientCompany, invoice.recipient_company_id)
        if company is not None:
            public.company = RecipientCompanyPublic.model_validate(company)
    return public


@router.get("/invoices", response_model=DataInvoicesPublic)
def list_invoices(
    session: SessionDep,
    scope: Annotated[GlobalOrCompany, Depends(require_export_access)],
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, le=200),
    state: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=255),
) -> DataInvoicesPublic:
    """Invoices visible to the key, newest first."""
    query = select(Invoice)
    if scope is not None:
        query = query.where(col(Invoice.recipient_company_id) == scope)
    if state is not None:
        try:
            state = InvoiceState(state).value
        except ValueError:
            raise HTTPException(status_code=400, detail="Neplatný stav faktury")
        query = query.where(Invoice.state == state)
    if q:
        needle = f"%{q}%"
        query = query.where(
            or_(
                col(Invoice.invoice_number).ilike(needle),
                col(Invoice.supplier_name).ilike(needle),
                col(Invoice.supplier_ico) == q,
            )
        )
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(col(Invoice.created_at).desc()).offset(skip).limit(limit)
    ).all()
    return DataInvoicesPublic(
        data=[_to_public(session, row) for row in rows], count=count
    )


@router.get("/invoices/{invoice_id}", response_model=DataInvoicePublic)
def get_invoice(
    session: SessionDep,
    invoice_id: UUID,
    scope: Annotated[GlobalOrCompany, Depends(require_export_access)],
) -> DataInvoicePublic:
    invoice = _scoped_invoice(session, scope, invoice_id)
    return _to_public(session, invoice)


@router.get("/invoices/{invoice_id}/file")
def get_invoice_file(
    session: SessionDep,
    invoice_id: UUID,
    scope: Annotated[GlobalOrCompany, Depends(require_export_access)],
) -> Response:
    """Original document as stored (any workflow state, read-only)."""
    invoice = _scoped_invoice(session, scope, invoice_id)
    try:
        data = storage.get_storage().open(invoice.storage_key)
    except FileNotFoundError, OSError:
        raise HTTPException(status_code=404, detail="Soubor nenalezen")
    return Response(content=data, media_type=invoice.content_type)
