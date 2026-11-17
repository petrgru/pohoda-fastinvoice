"""The machine-to-machine API behind the company X-API-Key header."""

from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import InvoiceState
from tests.utils.invoice import create_company, create_invoice

EXTERNAL = "/api/v1/external"


def make_api_key(
    client: TestClient, headers: dict[str, str], db: Session
) -> tuple[str, Any]:
    """A fresh company with one API key; returns (plaintext, company)."""
    company = create_company(db)
    response = client.post(
        f"/api/v1/companies/{company.id}/api-keys",
        headers=headers,
        json={"label": "ucetni aplikace"},
    )
    assert response.status_code == 201
    return response.json()["key"], company


DATA = "/api/v1/data"


def test_external_requires_a_valid_active_key(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    invoice = create_invoice(db, company=create_company(db))
    assert (
        client.patch(f"{EXTERNAL}/invoices/{invoice.id}", json={"supplier_name": "x"})
    ).status_code == 401

    key, _company = make_api_key(client, superuser_token_headers, db)
    headers = {"X-API-Key": key}
    assert client.get(f"{DATA}/invoices", headers=headers).status_code == 200

    # revoking cuts the external application off at once
    listing = client.get(
        f"/api/v1/companies/{_company.id}/api-keys", headers=superuser_token_headers
    ).json()
    key_id = listing[0]["id"]
    client.delete(
        f"/api/v1/companies/{_company.id}/api-keys/{key_id}",
        headers=superuser_token_headers,
    )
    assert client.get(f"{DATA}/invoices", headers=headers).status_code == 401


def test_external_key_only_sees_its_company(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    mine = create_invoice(db, company=company)
    other = create_invoice(db, company=create_company(db))

    headers = {"X-API-Key": key}
    body = client.get(f"{DATA}/invoices", headers=headers).json()
    assert [item["id"] for item in body["data"]] == [str(mine.id)]
    assert body["count"] == 1

    assert (
        client.get(f"{DATA}/invoices/{other.id}", headers=headers).status_code == 404
    )
    detail = client.get(f"{DATA}/invoices/{mine.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["supplier_name"] == mine.supplier_name


def test_external_can_edit_dates_and_vat_before_confirmation(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    invoice = create_invoice(
        db, company=company, delivery_date=None, kh_date=None, vat_items=None
    )
    headers = {"X-API-Key": key}

    response = client.patch(
        f"{EXTERNAL}/invoices/{invoice.id}",
        headers=headers,
        json={
            "delivery_date": str(date(2024, 3, 1)),
            "vat_items": [
                {
                    "vat_rate": "21%",
                    "base_price": "1000.00",
                    "tax_price": "210.00",
                    "total_price": "1210.00",
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["delivery_date"] == "2024-03-01"
    assert body["vat_check_ok"] is True

    # the mandatory-date gate also guards the external API
    response = client.patch(
        f"{EXTERNAL}/invoices/{invoice.id}",
        headers=headers,
        json={"due_date": None},
    )
    assert response.status_code == 422

    # the recipient company and bank accounts are out of the app's reach
    response = client.patch(
        f"{EXTERNAL}/invoices/{invoice.id}",
        headers=headers,
        json={
            "recipient_company_id": str(create_company(db).id),
            "supplier_iban": "CZ6508000000192000145399",
        },
    )
    assert response.status_code == 200
    assert response.json()["recipient_company_id"] == str(company.id)
    assert response.json()["supplier_iban"] is None


def test_external_status_flow_with_document_number(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    invoice = create_invoice(db, company=company, state=InvoiceState.READY_FOR_EXPORT)
    headers = {"X-API-Key": key}

    # an unapproved invoice cannot be imported
    pending = create_invoice(db, company=company)
    response = client.post(
        f"{EXTERNAL}/invoices/{pending.id}/status",
        headers=headers,
        json={"state": "exported", "document_number": "2024/1"},
    )
    assert response.status_code == 409

    # the document number is required for the import to succeed
    response = client.post(
        f"{EXTERNAL}/invoices/{invoice.id}/status",
        headers=headers,
        json={"state": "exported"},
    )
    assert response.status_code == 422

    response = client.post(
        f"{EXTERNAL}/invoices/{invoice.id}/status",
        headers=headers,
        json={"state": "exported", "document_number": "2024/1234"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "exported"
    assert body["external_number"] == "2024/1234"

    # reporting again is idempotent and may correct the number
    response = client.post(
        f"{EXTERNAL}/invoices/{invoice.id}/status",
        headers=headers,
        json={"state": "exported", "document_number": "2024/1235"},
    )
    assert response.json()["external_number"] == "2024/1235"

    # the imported invoice is locked: no edits here …
    assert (
        client.patch(
            f"{EXTERNAL}/invoices/{invoice.id}",
            headers=headers,
            json={"supplier_name": "NOPE"},
        ).status_code
        == 403
    )
    # … and no deletion in the web UI either
    assert (
        client.delete(
            f"/api/v1/invoices/{invoice.id}", headers=superuser_token_headers
        ).status_code
        == 403
    )


def test_external_failed_invoice_is_editable_and_requeueable(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    invoice = create_invoice(db, company=company, state=InvoiceState.READY_FOR_EXPORT)
    headers = {"X-API-Key": key}

    response = client.post(
        f"{EXTERNAL}/invoices/{invoice.id}/status",
        headers=headers,
        json={"state": "export_failed", "note": "nesedí DPH"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "export_failed"

    # correcting a failed invoice is exactly what the app must be able to do
    response = client.patch(
        f"{EXTERNAL}/invoices/{invoice.id}",
        headers=headers,
        json={"supplier_name": "Opraveny Dodavatel a.s."},
    )
    assert response.status_code == 200

    response = client.post(
        f"{EXTERNAL}/invoices/{invoice.id}/status",
        headers=headers,
        json={"state": "ready_for_export"},
    )
    assert response.json()["state"] == "ready_for_export"

    response = client.post(
        f"{EXTERNAL}/invoices/{invoice.id}/status",
        headers=headers,
        json={"state": "exported", "document_number": "2024/99"},
    )
    assert response.json()["state"] == "exported"


def test_api_key_listing_shows_first_and_last_chars(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    listing = client.get(
        f"/api/v1/companies/{company.id}/api-keys", headers=superuser_token_headers
    ).json()
    assert len(listing) == 1
    item = listing[0]
    assert item["key_prefix"] == key[:12]
    assert item["key_suffix"] == key[-6:]
    # the plaintext is never repeated in listings
    assert "key" not in item


def test_generating_a_key_replaces_the_previous_one(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    response = client.post(
        f"/api/v1/companies/{company.id}/api-keys",
        headers=superuser_token_headers,
        json={"label": "rotace"},
    )
    assert response.status_code == 201
    rotated = response.json()["key"]
    assert rotated != key

    # the old key is gone instantly, the new one is the only key
    assert (
        client.get(f"{DATA}/invoices", headers={"X-API-Key": key}).status_code == 401
    )
    assert (
        client.get(f"{DATA}/invoices", headers={"X-API-Key": rotated}).status_code
        == 200
    )
    listing = client.get(
        f"/api/v1/companies/{company.id}/api-keys", headers=superuser_token_headers
    ).json()
    # the replaced key is deleted, only the new one remains listed
    assert [item["label"] for item in listing] == ["rotace"]
