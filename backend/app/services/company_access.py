"""Per-company access rules: membership, visibility filters, API keys.

Superusers are implicitly admin of every company; every other user sees and
edits only the companies where they hold a membership. An invoice without a
company stays visible to the person who uploaded it (and to superusers).
"""

import hashlib
import secrets
import uuid

from fastapi import HTTPException
from sqlmodel import Session, col, or_, select

from app.core.config import settings
from app.models import (
    Company,
    CompanyApiKey,
    CompanyMembership,
    Invoice,
    MembershipRole,
    User,
    UserCreate,
    get_datetime_utc,
)

API_KEY_PREFIX = "fik_"


def role_in_company(
    session: Session, user: User, company_id: uuid.UUID
) -> MembershipRole | None:
    if user.is_superuser:
        return MembershipRole.ADMIN
    link = session.exec(
        select(CompanyMembership).where(
            col(CompanyMembership.user_id) == user.id,
            col(CompanyMembership.company_id) == company_id,
        )
    ).first()
    return MembershipRole(link.role) if link else None


def member_company_ids(session: Session, user: User) -> list[uuid.UUID] | None:
    """Company ids the user belongs to; None means "all companies"."""
    if user.is_superuser:
        return None
    return list(
        session.exec(
            select(CompanyMembership.company_id).where(
                col(CompanyMembership.user_id) == user.id
            )
        ).all()
    )


def invoice_filter(session: Session, user: User):
    """SQL condition limiting invoices to what the user may see (or None)."""
    ids = member_company_ids(session, user)
    if ids is None:
        return None
    return or_(col(Invoice.company_id).in_(ids), col(Invoice.uploaded_by_id) == user.id)


def company_filter(session: Session, user: User):
    """SQL condition limiting companies to the user's memberships (or None)."""
    ids = member_company_ids(session, user)
    if ids is None:
        return None
    return col(Company.id).in_(ids)


def ensure_invoice_access(
    session: Session,
    user: User,
    invoice: Invoice,
    min_role: MembershipRole = MembershipRole.EDITOR,
) -> None:
    """403 unless the user may act on this invoice with at least `min_role`."""
    if user.is_superuser:
        return
    if invoice.company_id is None:
        # unclassified: only the uploader may touch it
        if invoice.uploaded_by_id != user.id:
            raise HTTPException(status_code=403, detail="errors.forbidden")
        return
    role = role_in_company(session, user, invoice.company_id)
    if role is None or (
        min_role is MembershipRole.ADMIN and role is not MembershipRole.ADMIN
    ):
        raise HTTPException(status_code=403, detail="errors.forbidden")


def ensure_company_access(
    session: Session,
    user: User,
    company_id: uuid.UUID,
    min_role: MembershipRole = MembershipRole.EDITOR,
) -> MembershipRole:
    role = role_in_company(session, user, company_id)
    if role is None or (
        min_role is MembershipRole.ADMIN and role is not MembershipRole.ADMIN
    ):
        raise HTTPException(status_code=403, detail="errors.forbidden")
    return role


def ensure_company_selectable(
    session: Session, user: User, company_id: uuid.UUID
) -> None:
    """A company may be attached to an invoice only when the user belongs to it."""
    if user.is_superuser:
        return
    if company_id not in (member_company_ids(session, user) or []):
        raise HTTPException(status_code=403, detail="errors.forbidden")


def admin_link(
    session: Session, user: User, company_id: uuid.UUID
) -> CompanyMembership | None:
    return session.exec(
        select(CompanyMembership).where(
            col(CompanyMembership.user_id) == user.id,
            col(CompanyMembership.company_id) == company_id,
            col(CompanyMembership.role) == MembershipRole.ADMIN.value,
        )
    ).first()


def add_admin_member(session: Session, user: User, company: Company) -> None:
    """Make `user` an admin of `company` unless they already have a role there."""
    if role_in_company(session, user, company.id) is not None:
        return
    session.add(
        CompanyMembership(
            user_id=user.id, company_id=company.id, role=MembershipRole.ADMIN.value
        )
    )


def invite_user(session: Session, email: str) -> User:
    """Create a user with a random password and send them the setup link."""
    from app import crud
    from app.utils import (
        generate_company_invite_email,
        generate_password_reset_token,
        send_email,
    )

    created = crud.create_user(
        session=session,
        user_create=UserCreate(email=email, password=secrets.token_urlsafe(24)),
    )
    if settings.emails_enabled:
        token = generate_password_reset_token(email)
        email_data = generate_company_invite_email(
            email_to=email, email=email, token=token
        )
        send_email(
            email_to=email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )
    return created


# --- API keys ---------------------------------------------------------------


def generate_api_key() -> tuple[str, str]:
    """Return (plaintext, sha256 hex) for a new company API key."""
    plaintext = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return plaintext, hashlib.sha256(plaintext.encode()).hexdigest()


def resolve_api_key(session: Session, value: str) -> CompanyApiKey | None:
    """Active key matching the secret, marking it used; None when unknown/revoked."""
    key = session.exec(
        select(CompanyApiKey).where(
            CompanyApiKey.key_hash == hashlib.sha256(value.encode()).hexdigest()
        )
    ).first()
    if key is None or key.revoked_at is not None:
        return None
    key.last_used_at = get_datetime_utc()
    session.add(key)
    session.commit()
    return key
