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
    CompanyApiKey,
    CompanyApiKeyCreate,
    CompanyApiKeyCreated,
    CompanyApiKeyPublic,
    CompanyCreate,
    CompanyMembership,
    CompanyPublic,
    CompanyUpdate,
    Invoice,
    MembershipCreate,
    MembershipPublic,
    MembershipRole,
    MembershipUpdate,
    Message,
    User,
    VatInfoPublic,
    get_datetime_utc,
)
from app.services import companies as company_service
from app.services import company_access, search_index, storage, vat
from app.services import invoices as invoice_service

router = APIRouter(prefix="/companies", tags=["companies"])

logger = logging.getLogger(__name__)


def _get_company_or_404(session, company_id: UUID) -> Company:  # noqa: ANN001
    company = session.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="errors.company_not_found")
    return company


def _with_roles(session, user, companies: list[Company]) -> list[CompanyPublic]:  # noqa: ANN001
    """CompanyPublic items carrying the requesting user's role per company."""
    if user.is_superuser:
        roles: dict[UUID, MembershipRole] = {}
    else:
        links = session.exec(
            select(CompanyMembership).where(col(CompanyMembership.user_id) == user.id)
        ).all()
        roles = {link.company_id: MembershipRole(link.role) for link in links}
    items = []
    for company in companies:
        public = CompanyPublic.model_validate(company)
        public.my_role = MembershipRole.ADMIN if user.is_superuser else roles.get(
            company.id
        )
        items.append(public)
    return items


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
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 50,
    q: str | None = None,
    include_inactive: bool = False,
) -> CompaniesPublic:
    conditions: list[ColumnElement[bool]] = (
        [] if include_inactive else [col(Company.is_active) == True]  # noqa: E712
    )
    if (visible := company_access.company_filter(session, current_user)) is not None:
        conditions.append(visible)
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
        data=_with_roles(session, current_user, list(items)), count=count
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
    # the creator is always an admin of a manually created company
    company_access.add_admin_member(session, current_user, company)
    session.commit()
    invoice_service.log_audit(
        session, "company_created", company=company, user=current_user
    )
    session.commit()
    session.refresh(company)
    return _with_roles(session, current_user, [company])[0]


@router.get("/{company_id}", response_model=CompanyPublic)
def get_company(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
) -> CompanyPublic:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(session, current_user, company.id)
    return _with_roles(session, current_user, [company])[0]


@router.put("/{company_id}", response_model=CompanyPublic)
def update_company(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
    company_in: CompanyUpdate,
) -> CompanyPublic:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
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
    return _with_roles(session, current_user, [company])[0]


@router.delete("/{company_id}", response_model=Message)
def delete_company(
    *, session: SessionDep, current_user: CurrentUser, company_id: UUID
) -> Message:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
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


# --- members ----------------------------------------------------------------


def _membership_public(session, link: CompanyMembership) -> MembershipPublic:  # noqa: ANN001
    user = session.get(User, link.user_id)
    return MembershipPublic(
        id=link.id,
        user_id=link.user_id,
        email=user.email if user else "unknown@invalid",
        full_name=user.full_name if user else None,
        is_active=user.is_active if user else False,
        role=MembershipRole(link.role),
        created_at=link.created_at,
    )


def _get_membership_or_404(session, company_id: UUID, membership_id: UUID):  # noqa: ANN001
    link = session.get(CompanyMembership, membership_id)
    if link is None or link.company_id != company_id:
        raise HTTPException(status_code=404, detail="errors.member_not_found")
    return link


def _assert_not_last_admin(
    session, link: CompanyMembership, new_role: MembershipRole
) -> None:  # noqa: ANN001
    if new_role is MembershipRole.ADMIN:
        return
    remaining_admins = session.exec(
        select(func.count())
        .select_from(CompanyMembership)
        .where(
            col(CompanyMembership.company_id) == link.company_id,
            col(CompanyMembership.role) == MembershipRole.ADMIN.value,
            col(CompanyMembership.id) != link.id,
        )
    ).one()
    other_admin_exists = session.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    if not remaining_admins and not other_admin_exists:
        raise HTTPException(status_code=409, detail="errors.last_company_admin")


@router.get("/{company_id}/members", response_model=list[MembershipPublic])
def list_members(
    *, session: SessionDep, current_user: CurrentUser, company_id: UUID
) -> list[MembershipPublic]:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
    links = session.exec(
        select(CompanyMembership)
        .where(col(CompanyMembership.company_id) == company.id)
        .order_by(col(CompanyMembership.created_at).asc())
    ).all()
    members = [_membership_public(session, link) for link in links]
    if current_user.is_superuser:
        already = {m.user_id for m in members}
        if current_user.id not in already:
            members.append(
                MembershipPublic(
                    id=current_user.id,
                    user_id=current_user.id,
                    email=current_user.email,
                    full_name=current_user.full_name,
                    is_active=current_user.is_active,
                    role=MembershipRole.ADMIN,
                    created_at=None,
                )
            )
    return members


@router.post("/{company_id}/members", response_model=MembershipPublic, status_code=201)
def add_member(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
    member_in: MembershipCreate,
) -> MembershipPublic:
    """Add an existing user by e-mail; an unknown address is invited by e-mail."""
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
    email = member_in.email.lower()
    user = session.exec(select(User).where(col(User.email) == email)).first()
    if user is None:
        user = company_access.invite_user(session, email)
        invoice_service.log_audit(
            session,
            "member_invited",
            company=company,
            user=current_user,
            details={"email": email, "role": member_in.role.value},
        )
    existing = session.exec(
        select(CompanyMembership).where(
            col(CompanyMembership.user_id) == user.id,
            col(CompanyMembership.company_id) == company.id,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="errors.member_exists")
    link = CompanyMembership(
        user_id=user.id, company_id=company.id, role=member_in.role.value
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    invoice_service.log_audit(
        session,
        "member_added",
        company=company,
        user=current_user,
        details={"email": email, "role": member_in.role.value},
    )
    session.commit()
    return _membership_public(session, link)


@router.patch("/{company_id}/members/{membership_id}", response_model=MembershipPublic)
def update_member_role(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
    membership_id: UUID,
    member_in: MembershipUpdate,
) -> MembershipPublic:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
    link = _get_membership_or_404(session, company_id, membership_id)
    _assert_not_last_admin(session, link, member_in.role)
    link.role = member_in.role.value
    session.add(link)
    invoice_service.log_audit(
        session,
        "member_role_changed",
        company=company,
        user=current_user,
        details={"role": member_in.role.value},
    )
    session.commit()
    session.refresh(link)
    return _membership_public(session, link)


@router.delete("/{company_id}/members/{membership_id}", response_model=Message)
def remove_member(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
    membership_id: UUID,
) -> Message:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
    link = _get_membership_or_404(session, company_id, membership_id)
    _assert_not_last_admin(session, link, MembershipRole.EDITOR)
    invoice_service.log_audit(
        session, "member_removed", company=company, user=current_user
    )
    session.delete(link)
    session.commit()
    return Message(message="Člen odstraněn")


@router.get("/user-lookup/{email}", response_model=list[MembershipPublic])
def lookup_user_by_email(
    *, session: SessionDep, current_user: CurrentUser, email: str  # noqa: ARG001
) -> list[MembershipPublic]:
    """Match user accounts by e-mail fragment for the member picker."""
    users = session.exec(
        select(User)
        .where(col(User.email).ilike(f"%{email}%"))
        .order_by(col(User.email).asc())
        .limit(5)
    ).all()
    return [
        MembershipPublic(
            id=user.id,
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            role=MembershipRole.EDITOR,  # placeholder until membership is known
            created_at=user.created_at,
        )
        for user in users
    ]


# --- API keys ---------------------------------------------------------------


@router.get("/{company_id}/api-keys", response_model=list[CompanyApiKeyPublic])
def list_api_keys(
    *, session: SessionDep, current_user: CurrentUser, company_id: UUID
) -> list[CompanyApiKeyPublic]:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
    keys = session.exec(
        select(CompanyApiKey)
        .where(col(CompanyApiKey.company_id) == company.id)
        .order_by(col(CompanyApiKey.created_at).desc())
    ).all()
    return [CompanyApiKeyPublic.model_validate(k) for k in keys]


@router.post(
    "/{company_id}/api-keys", response_model=CompanyApiKeyCreated, status_code=201
)
def create_api_key(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
    key_in: CompanyApiKeyCreate,
) -> CompanyApiKeyCreated:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
    plaintext, key_hash = company_access.generate_api_key()
    key = CompanyApiKey(
        company_id=company.id,
        label=key_in.label,
        key_prefix=plaintext[:12],
        key_hash=key_hash,
        created_by_id=current_user.id,
    )
    session.add(key)
    invoice_service.log_audit(
        session,
        "api_key_created",
        company=company,
        user=current_user,
        details={"label": key_in.label},
    )
    session.commit()
    session.refresh(key)
    return CompanyApiKeyCreated(
        **CompanyApiKeyPublic.model_validate(key).model_dump(), key=plaintext
    )


@router.delete("/{company_id}/api-keys/{key_id}", response_model=Message)
def revoke_api_key(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    company_id: UUID,
    key_id: UUID,
) -> Message:
    company = _get_company_or_404(session, company_id)
    company_access.ensure_company_access(
        session, current_user, company.id, MembershipRole.ADMIN
    )
    key = session.get(CompanyApiKey, key_id)
    if key is None or key.company_id != company.id:
        raise HTTPException(status_code=404, detail="errors.api_key_not_found")
    key.revoked_at = get_datetime_utc()
    session.add(key)
    invoice_service.log_audit(
        session,
        "api_key_revoked",
        company=company,
        user=current_user,
        details={"label": key.label},
    )
    session.commit()
    return Message(message="Klíč zrušen")
