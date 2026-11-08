"""Supplier registry: vendors seen on imported invoices.

Suppliers carry no own membership – they are visible exactly when the user
sees at least one invoice from them (linked by IČO, fallback by name).
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import ColumnElement, func
from sqlmodel import Session, col, or_, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Invoice,
    InvoicesPublic,
    Supplier,
    SupplierKnownAccount,
    SupplierPublic,
    SuppliersPublic,
    SupplierUpdate,
    User,
    get_datetime_utc,
)
from app.services import invoices as invoice_service
from app.services import recipient_access

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


def _visibility_condition(session: Session, user: User) -> ColumnElement[bool] | None:
    """Suppliers with at least one invoice the user may see (None = all)."""
    visible = recipient_access.invoice_filter(session, user)
    if visible is None:
        return None
    visible_icos = select(Invoice.supplier_ico).where(visible)
    visible_names = select(Invoice.supplier_name).where(visible)
    return or_(
        col(Supplier.ico).in_(visible_icos), col(Supplier.name).in_(visible_names)
    )


def _get_supplier_or_404(
    session: Session,
    current_user: User,
    supplier_id: UUID,
) -> Supplier:
    supplier: Supplier | None = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="errors.supplier_not_found")
    condition = _visibility_condition(session, current_user)
    if condition is not None:
        visible = session.exec(
            select(Supplier.id).where(col(Supplier.id) == supplier_id, condition)
        ).first()
        if visible is None:
            raise HTTPException(status_code=404, detail="errors.supplier_not_found")
    return supplier


def _public(session: Session, supplier: Supplier, *, user: User) -> SupplierPublic:
    visible = recipient_access.invoice_filter(session, user)
    conditions: list[ColumnElement[bool]] = [
        or_(
            col(Invoice.supplier_ico) == supplier.ico,
            col(Invoice.supplier_name) == supplier.name,
        )
    ]
    if visible is not None:
        conditions.append(visible)
    count, last = session.exec(
        select(func.count(), func.max(Invoice.created_at)).where(*conditions)
    ).one()
    return SupplierPublic(
        id=supplier.id,
        name=supplier.name,
        ico=supplier.ico,
        dic=supplier.dic,
        bank_accounts=[
            SupplierKnownAccount.model_validate(row)
            for row in supplier.bank_accounts or []
        ],
        invoice_count=int(count or 0),
        last_invoice_at=last,
    )


@router.get("", response_model=SuppliersPublic)
def list_suppliers(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 50,
    q: str | None = None,
) -> SuppliersPublic:
    conditions: list[ColumnElement[bool]] = []
    if (visibility := _visibility_condition(session, current_user)) is not None:
        conditions.append(visibility)
    if q:
        needle = f"%{q.strip()}%"
        conditions.append(
            or_(
                col(Supplier.name).ilike(needle),
                col(Supplier.ico).ilike(needle),
                col(Supplier.dic).ilike(needle),
            )
        )
    query = select(Supplier)
    for condition in conditions:
        query = query.where(condition)
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(col(Supplier.name).asc()).offset(skip).limit(limit)
    ).all()
    return SuppliersPublic(
        data=[_public(session, row, user=current_user) for row in rows],
        count=int(total),
    )


@router.get("/{supplier_id}", response_model=SupplierPublic)
def get_supplier(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    supplier_id: UUID,
) -> SupplierPublic:
    supplier = _get_supplier_or_404(session, current_user, supplier_id)
    return _public(session, supplier, user=current_user)


@router.get("/{supplier_id}/invoices", response_model=InvoicesPublic)
def supplier_invoices(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    supplier_id: UUID,
    skip: int = 0,
    limit: int = 50,
) -> InvoicesPublic:
    supplier = _get_supplier_or_404(session, current_user, supplier_id)
    conditions: list[ColumnElement[bool]] = [
        or_(
            col(Invoice.supplier_ico) == supplier.ico,
            col(Invoice.supplier_name) == supplier.name,
        )
    ]
    if (visible := recipient_access.invoice_filter(session, current_user)) is not None:
        conditions.append(visible)
    query = select(Invoice)
    for condition in conditions:
        query = query.where(condition)
    rows = session.exec(
        query.order_by(col(Invoice.created_at).desc()).offset(skip).limit(limit)
    ).all()
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    return InvoicesPublic(
        data=[invoice_service.to_public(session, row) for row in rows],
        count=int(total),
    )


@router.put("/{supplier_id}", response_model=SupplierPublic)
def update_supplier(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    supplier_id: UUID,
    supplier_in: SupplierUpdate,
) -> SupplierPublic:
    supplier = _get_supplier_or_404(session, current_user, supplier_id)
    changes = supplier_in.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if field == "bank_accounts" and value is not None:
            value = [
                row.model_dump(mode="json")
                for row in supplier_in.bank_accounts or []
                if row.account or row.iban
            ]
        setattr(supplier, field, value)
    supplier.updated_at = get_datetime_utc()
    session.add(supplier)
    session.commit()
    session.refresh(supplier)
    return _public(session, supplier, user=current_user)
