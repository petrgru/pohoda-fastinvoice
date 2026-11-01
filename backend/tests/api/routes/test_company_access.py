"""Per-company membership, visibility isolation and per-company API keys."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.core.config import settings
from app.models import CompanyMembership, InvoiceState, MembershipRole, User
from tests.utils.invoice import create_company, create_invoice
from tests.utils.user import authentication_token_from_email

EXPORT = "/api/v1/export"


def unique_email() -> str:
    return f"user-{uuid4().hex[:10]}@example.com"


def member_headers(
    client: TestClient, db: Session, email: str | None = None
) -> tuple[dict[str, str], str]:
    email = email or unique_email()
    headers = authentication_token_from_email(client=client, email=email, db=db)
    return headers, email


def add_membership(
    db: Session, user_email: str, company_id: object, role: MembershipRole
) -> None:
    user = db.exec(select(User).where(col(User.email) == user_email)).one()
    db.add(
        CompanyMembership(
            user_id=user.id,
            company_id=company_id,
            role=role.value,  # type: ignore[arg-type]
        )
    )
    db.commit()


def test_user_without_membership_sees_nothing(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    company = create_company(db)
    create_invoice(db, company=company)

    headers, _ = member_headers(client, db)
    response = client.get("/api/v1/invoices", headers=headers)
    assert response.status_code == 200
    assert response.json()["count"] == 0

    # superuser still sees everything
    response = client.get("/api/v1/invoices", headers=superuser_token_headers)
    assert response.json()["count"] >= 1

    # and the company list is empty for a stranger
    response = client.get("/api/v1/companies", headers=headers)
    assert response.json()["count"] == 0


def test_editor_visibility_and_isolation(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    company_a = create_company(db)
    company_b = create_company(db)
    invoice_a = create_invoice(db, company=company_a)
    invoice_b = create_invoice(db, company=company_b)

    headers, email = member_headers(client, db)
    add_membership(db, email, company_a.id, MembershipRole.EDITOR)

    response = client.get("/api/v1/invoices", headers=headers)
    ids = [item["id"] for item in response.json()["data"]]
    assert str(invoice_a.id) in ids
    assert str(invoice_b.id) not in ids

    # queue and stats follow the same scope
    response = client.get("/api/v1/invoices/queue", headers=headers)
    ids = [item["id"] for item in response.json()["data"]]
    assert str(invoice_a.id) in ids
    assert str(invoice_b.id) not in ids

    response = client.get("/api/v1/invoices/stats", headers=headers)
    assert response.json()["totals"]["needs_review"] == 1

    # detail access respects the boundary
    assert (
        client.get(f"/api/v1/invoices/{invoice_a.id}", headers=headers).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/invoices/{invoice_b.id}", headers=headers).status_code
        == 403
    )
    # editor may approve but not delete
    assert (
        client.post(
            f"/api/v1/invoices/{invoice_a.id}/approve", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.delete(f"/api/v1/invoices/{invoice_b.id}", headers=headers).status_code
        == 403
    )


def test_management_endpoints_require_company_admin(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    company = create_company(db)
    editor_headers, editor_email = member_headers(client, db)
    add_membership(db, editor_email, company.id, MembershipRole.EDITOR)

    assert (
        client.get(
            f"/api/v1/companies/{company.id}/members", headers=editor_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/companies/{company.id}/api-keys",
            headers=editor_headers,
            json={"label": "pohoda"},
        ).status_code
        == 403
    )

    admin_headers, admin_email = member_headers(client, db)
    add_membership(db, admin_email, company.id, MembershipRole.ADMIN)
    response = client.get(
        f"/api/v1/companies/{company.id}/members", headers=admin_headers
    )
    assert response.status_code == 200
    emails = {item["email"] for item in response.json()}
    assert admin_email in emails


def test_add_member_by_email_invites_unknown_user(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    response = client.post(
        "/api/v1/companies",
        headers=superuser_token_headers,
        json={"name": "Firma Pozvaná", "ico": "86362488", "bank_account": "1/0100"},
    )
    assert response.status_code == 201
    company_id = response.json()["id"]

    new_email = unique_email()
    response = client.post(
        f"/api/v1/companies/{company_id}/members",
        headers=superuser_token_headers,
        json={"email": new_email, "role": MembershipRole.EDITOR.value},
    )
    assert response.status_code == 201
    assert db.exec(select(User).where(col(User.email) == new_email)).first() is not None

    # a second add of the same user is a conflict
    response = client.post(
        f"/api/v1/companies/{company_id}/members",
        headers=superuser_token_headers,
        json={"email": new_email, "role": MembershipRole.EDITOR.value},
    )
    assert response.status_code == 409


def test_company_api_key_scopes_export(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "EXPORT_API_KEY", "")
    approver = db.exec(select(User)).first()
    company_a = create_company(db)
    company_b = create_company(db)
    invoice_a = create_invoice(
        db, InvoiceState.READY_FOR_EXPORT, company=company_a, approved_by=approver.id
    )
    invoice_b = create_invoice(
        db, InvoiceState.READY_FOR_EXPORT, company=company_b, approved_by=approver.id
    )

    response = client.post(
        f"/api/v1/companies/{company_a.id}/api-keys",
        headers=superuser_token_headers,
        json={"label": "connector A"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["key"].startswith("fik_")
    key_headers = {"X-API-Key": created["key"]}

    response = client.get(f"{EXPORT}/queue", headers=key_headers)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert str(invoice_a.id) in ids
    assert str(invoice_b.id) not in ids

    # reporting a result for another company's invoice is invisible
    response = client.post(
        f"{EXPORT}/result",
        headers=key_headers,
        json={"invoice_id": str(invoice_b.id), "success": True},
    )
    assert response.status_code == 404

    # company sync has been retired
    response = client.put(
        f"{EXPORT}/companies/sync",
        headers=key_headers,
        json={"companies": [{"name": "X", "ico": "12345678"}]},
    )
    assert response.status_code == 410

    # wrong key is rejected
    assert (
        client.get(f"{EXPORT}/queue", headers={"X-API-Key": "fik_nonsense"}).status_code
        == 401
    )

    # revoke -> 401, key listed as revoked
    key_id = created["id"]
    assert (
        client.delete(
            f"/api/v1/companies/{company_a.id}/api-keys/{key_id}",
            headers=superuser_token_headers,
        ).status_code
        == 200
    )
    # the revoked key is dead; with no other key it disables the API entirely
    assert client.get(f"{EXPORT}/queue", headers=key_headers).status_code == 503
    keys = client.get(
        f"/api/v1/companies/{company_a.id}/api-keys",
        headers=superuser_token_headers,
    ).json()
    assert keys[0]["revoked_at"] is not None
