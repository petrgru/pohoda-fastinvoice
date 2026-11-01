import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import ColumnElement, func, or_
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.models import (
    CompaniesPublic,
    Company,
    CompanyCreate,
    CompanyPublic,
    CompanyUpdate,
    Invoice,
    Message,
    VatInfoPublic,
    get_datetime_utc,
)
from app.services import companies as company_service
from app.services import invoices as invoice_service
from app.services import search_index, storage, vat

router = APIRouter(prefix="/companies", tags=["companies"])

logger = logging.getLogger(__name__)


def assert_dic_valid(dic: str | None) -> None:
    """Block saving on a VAT number VIES reports as invalid; when the external
    service is unreachable keep saving possible (fail-open) with a log entry."""
    if not dic:
        return
    try:
        result = vat.check_dic_or_raise(dic)
    except vat.VatInvalidError as exc:
        raise HTTPException(status_code=422, detail="errors.vat_invalid") from exc
    if result is not None and result.unavailable:
        logger.warning("VAT service unreachable, skipping DIČ check for %s", dic)


@router.get("", response_model=CompaniesPublic)
def list_companies(
    *,
    session: SessionDep,
    current_user: CurrentUser,  # noqa: ARG001
    skip: int = 0,
    limit: int = 50,
    q: str | None = None,
    include_inactive: bool = False,
) -> CompaniesPublic:
    conditions: list[ColumnElement[bool]] = (
        [] if include_inactive else [col(Company.is_active) == True]  # noqa: E712
    )
    if q:
        digits = "".join(char for char in q if char.isdigit())
        ilike = f"%{q}%"
        clauses = [col(Company.name).ilike(ilike)]
        if digits:
            clauses.append(col(Company.ico).like(f"%{digits}%"))
        conditions.append(or_(*clauses))
    query = select(Company)
    count_query = select(func.count()).select_from(Company)
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)
    count = session.exec(count_query).one()
    items = session.exec(
        query.order_by(col(Company.name).asc()).offset(skip).limit(limit)
    ).all()
    return CompaniesPublic(
        data=[CompanyPublic.model_validate(i) for i in items], count=count
    )


@router.get("/vat-info", response_model=VatInfoPublic)
def get_vat_info(
    *,
    current_user: CurrentUser,  # noqa: ARG001
    dic: str,
) -> VatInfoPublic:
    """VIES check + registered name/address for company form autofill."""
    result = vat.lookup_vat(dic)
    if result.unavailable:  # disabled or VIES unreachable
        raise HTTPException(status_code=502, detail="errors.vat_service_unavailable")
    return VatInfoPublic(
        valid=result.valid,
        name=result.name,
        address=result.address,
        country_code=result.country_code,
    )


@router.post("", response_model=CompanyPublic, status_code=201)
def create_company(
    *, session: SessionDep, current_user: CurrentUser, company_in: CompanyCreate
) -> CompanyPublic:
    assert_dic_valid(company_in.dic)
    existing = session.exec(
        select(Company).where(Company.ico == company_in.ico)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="errors.company_ico_exists")
    company = Company.model_validate(company_in.model_dump(exclude={"bank_accounts"}))
    session.add(company)
    session.flush()
    company_service.apply_bank_accounts(session, company, company_in.bank_accounts)
    session.commit()
    invoice_service.log_audit(
        session, "company_created", company=company, user=current_user
    )
    session.commit()
    session.refresh(company)
    return CompanyPublic.model_validate(company)


@router.get("/{company_id}", response_model=CompanyPublic)
def get_company(
    *,
    session: SessionDep,
    current_user: CurrentUser,  # noqa: ARG001
    company_id: UUID,
) -> CompanyPublic:
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="errors.company_not_found")
    return CompanyPublic.model_validate(company)


@router.put("/{company_id}", response_model=CompanyPublic)
def update_company(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
    company_in: CompanyUpdate,
) -> CompanyPublic:
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="errors.company_not_found")
    changes = company_in.model_dump(exclude_unset=True)
    bank_rows = company_in.bank_accounts if "bank_accounts" in changes else None
    changes.pop("bank_accounts", None)
    if changes.get("dic"):
        assert_dic_valid(changes["dic"])
    if "ico" in changes:
        clash = session.exec(
            select(Company).where(
                Company.ico == changes["ico"], col(Company.id) != company_id
            )
        ).first()
        if clash:
            raise HTTPException(status_code=409, detail="errors.company_ico_exists")
    for field, value in changes.items():
        setattr(company, field, value)
    company_service.apply_bank_accounts(session, company, bank_rows)
    company.updated_at = get_datetime_utc()
    session.add(company)
    invoice_service.log_audit(
        session,
        "company_updated",
        company=company,
        user=current_user,
        details={"fields": sorted(changes)},
    )
    session.commit()
    session.refresh(company)
    if "name" in changes:
        # the company name is part of the invoice search documents
        for invoice in session.exec(
            select(Invoice).where(Invoice.company_id == company_id)
        ).all():
            search_index.sync(session, invoice)
    return CompanyPublic.model_validate(company)


@router.delete("/{company_id}", response_model=Message)
def delete_company(
    *, session: SessionDep, current_user: CurrentUser, company_id: UUID
) -> Message:
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="errors.company_not_found")
    linked_rows = session.exec(
        select(Invoice).where(Invoice.company_id == company_id)
    ).all()
    if linked_rows and not settings.DEVELOP:
        raise HTTPException(
            status_code=409,
            detail=f"errors.company_has_invoices|{len(linked_rows)}",
        )
    if linked_rows:
        # DEVELOP mode: drop the company together with all its documents
        for invoice in linked_rows:
            try:
                storage.get_storage().delete(invoice.storage_key)
            except FileNotFoundError, OSError:
                pass
            invoice_id = invoice.id
            session.delete(invoice)
            search_index.remove(invoice_id)
    invoice_service.log_audit(
        session,
        "company_deleted",
        company=company,
        user=current_user,
        details={"cascade_invoices": len(linked_rows)},
    )
    session.delete(company)
    session.commit()
    return Message(message="Firma smazána")
