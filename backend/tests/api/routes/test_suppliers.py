import random
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import (
    Invoice,
    InvoiceState,
    Supplier,
    User,
)
from app.services import account_integrity
from tests.utils.invoice import create_company, create_invoice

API = "/api/v1/suppliers"


def _supplier(session: Session, **overrides: object) -> Supplier:
    data: dict = {
        "name": f"Big Supplier {uuid4().hex[:6]} a.s.",
        "ico": str(random.randrange(10000000, 99999999)),
        "bank_accounts": [{"account": "123456789/0100", "iban": None, "confirmed": False}],
    }
    data.update(overrides)
    supplier = Supplier(**data)
    session.add(supplier)
    session.commit()
    session.refresh(supplier)
    return supplier


def test_list_suppliers_with_counts(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    supplier = _supplier(db)
    create_invoice(db, supplier_ico=supplier.ico, supplier_name=supplier.name)
    response = client.get(API, headers=superuser_token_headers)
    assert response.status_code == 200
    rows = {row["ico"]: row for row in response.json()["data"]}
    assert supplier.ico in rows
    assert rows[supplier.ico]["invoice_count"] == 1


def test_update_supplier(client: TestClient, superuser_token_headers: dict[str, str], db: Session):
    supplier = _supplier(db)
    response = client.put(
        f"{API}/{supplier.id}",
        headers=superuser_token_headers,
        json={
            "name": "Prejmenovany a.s.",
            "bank_accounts": [
                {"account": "999999/0100", "iban": None, "confirmed": True}
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Prejmenovany a.s."
    assert response.json()["bank_accounts"][0]["confirmed"] is True


def test_supplier_invalid_account_blocks(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    supplier = _supplier(db)
    response = client.put(
        f"{API}/{supplier.id}",
        headers=superuser_token_headers,
        json={"bank_accounts": [{"account": "x/y/z", "iban": None, "confirmed": True}]},
    )
    assert response.status_code == 422


def test_suppliers_scoped_to_visible_invoices(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
):
    """A plain member sees only suppliers of invoices of their companies."""
    supplier = _supplier(db, name="Viditelny dodavatel a.s.")
    company = create_company(db)
    create_invoice(
        db, supplier_ico=supplier.ico, supplier_name=supplier.name, company=company
    )
    # hidden: invoice without a company belongs to nobody else than its uploader
    hidden = _supplier(db, name="Skryty dodavatel a.s.")
    create_invoice(db, supplier_ico=hidden.ico, supplier_name=hidden.name)

    visible = client.get(API, headers=normal_user_token_headers).json()["data"]
    # the member is not a member of `company` -> they see nothing (only their
    # own uploads)
    assert visible == []

    from sqlmodel import select

    from app.core.config import settings
    from app.models import MembershipRole, RecipientMembership

    user = db.exec(select(User).where(User.email == settings.EMAIL_TEST_USER)).one()
    db.add(
        RecipientMembership(
            user_id=user.id,
            recipient_company_id=company.id,
            role=MembershipRole.EDITOR.value,
        )
    )
    db.commit()
    visible = client.get(API, headers=normal_user_token_headers).json()["data"]
    # distinct supplier names – suppliers sharing a name are legitimately the
    # same vendor (name is the dedup key when an invoice has no IČO)
    icos = {row["ico"] for row in visible}
    assert supplier.ico in icos
    assert hidden.ico not in icos


def test_account_change_requires_confirmation_then_confirms(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    """The tampering flow: 409 without the flag, confirmation promotes the
    account in the supplier registry as the new reference."""
    ico = str(random.randrange(10000000, 99999999))
    document_account = "123456789/0100"
    invoice = create_invoice(
        db,
        supplier_ico=ico,
        supplier_bank_account=document_account,
        parsed_snapshot={"supplier_bank_account": document_account, "supplier_ico": ico},
    )
    account_integrity.upsert_from_invoice(db, invoice)
    db.commit()

    response = client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=superuser_token_headers,
        json={"supplier_bank_account": "555555555/0300"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "errors.account_changed_confirm"

    response = client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=superuser_token_headers,
        json={
            "supplier_bank_account": "555555555/0300",
            "confirm_account_change": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account_confirmed_at"] is not None
    assert body["account_matches_document"] is False

    supplier = account_integrity.find_supplier(db, invoice)
    assert supplier is not None
    db.refresh(supplier)
    confirmed = {
        row["account"]: row["confirmed"] for row in supplier.bank_accounts or []
    }
    assert confirmed["555555555/0300"] is True

    # a later invoice with the confirmed account needs no further confirmation
    later = create_invoice(
        db,
        supplier_ico=ico,
        supplier_bank_account="555555555/0300",
        parsed_snapshot={"supplier_bank_account": "555555555/0300", "supplier_ico": ico},
    )
    response = client.put(
        f"/api/v1/invoices/{later.id}",
        headers=superuser_token_headers,
        json={"supplier_bank_account": "555555555/0300", "invoice_number": "2024000999"},
    )
    assert response.status_code == 200


def test_account_unchanged_needs_no_confirmation(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    invoice = create_invoice(
        db,
        supplier_bank_account="123456789/0100",
        parsed_snapshot={"supplier_bank_account": "123456789/0100"},
    )
    response = client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=superuser_token_headers,
        json={
            "supplier_bank_account": "123456789/0100",
            "invoice_number": "2024000555",
        },
    )
    assert response.status_code == 200


def test_account_matches_document_flag(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    invoice = create_invoice(
        db,
        supplier_bank_account="999999999/0300",
        parsed_snapshot={"supplier_bank_account": "123456789/0100"},
    )
    detail = client.get(
        f"/api/v1/invoices/{invoice.id}", headers=superuser_token_headers
    ).json()
    assert detail["account_matches_document"] is False
