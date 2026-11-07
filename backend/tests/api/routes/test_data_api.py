"""Data API: read-only access keyed per recipient company."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.models import InvoiceState, RecipientCompany
from app.services import storage
from tests.utils.invoice import create_company, create_invoice

DATA = "/api/v1/data"
GLOBAL_KEY = "test-export-key"


@pytest.fixture
def global_key(monkeypatch):
    monkeypatch.setattr(settings, "EXPORT_API_KEY", GLOBAL_KEY)
    return {"X-API-Key": GLOBAL_KEY}


@pytest.fixture
def storage_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", tmp_path)
    return tmp_path


def issue_company_key(
    client: TestClient, headers: dict[str, str], company: RecipientCompany
) -> dict[str, str]:
    response = client.post(
        f"/api/v1/companies/{company.id}/api-keys",
        headers=headers,
        json={"label": "data app"},
    )
    assert response.status_code == 201
    return {"X-API-Key": response.json()["key"]}


def test_data_api_disabled_without_any_key(
    client: TestClient, monkeypatch, db: Session
):
    from sqlmodel import delete

    from app.models import RecipientApiKey

    monkeypatch.setattr(settings, "EXPORT_API_KEY", None)
    db.execute(delete(RecipientApiKey))
    db.commit()
    assert client.get(f"{DATA}/invoices").status_code == 503


def test_data_api_rejects_missing_or_wrong_key(client: TestClient, global_key):
    assert client.get(f"{DATA}/invoices").status_code == 401
    assert (
        client.get(f"{DATA}/invoices", headers={"X-API-Key": "wrong"}).status_code
        == 401
    )


def test_global_key_sees_all_invoices(client: TestClient, db: Session, global_key):
    company = create_company(db)
    assigned = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)
    unassigned = create_invoice(db, InvoiceState.NEEDS_REVIEW)

    listed = client.get(f"{DATA}/invoices", headers=global_key).json()
    ids = [item["id"] for item in listed["data"]]
    assert str(assigned.id) in ids
    assert str(unassigned.id) in ids
    assert listed["count"] >= 2

    got = client.get(f"{DATA}/invoices/{assigned.id}", headers=global_key)
    assert got.status_code == 200
    body = got.json()
    assert body["company"]["id"] == str(company.id)
    assert body["total_price"] == "1210.00"

    assert (
        client.get(
            f"{DATA}/invoices/29a9f7b0-0000-0000-0000-000000000000",
            headers=global_key,
        ).status_code
        == 404
    )


def test_state_and_search_filters(client: TestClient, db: Session, global_key):
    create_invoice(db, InvoiceState.NEEDS_REVIEW, invoice_number="DN-2024001")
    create_invoice(db, InvoiceState.EXPORTED, invoice_number="DN-2024002")

    response = client.get(
        f"{DATA}/invoices", headers=global_key, params={"state": "exported", "q": "DN-"}
    )
    assert response.status_code == 200
    body = response.json()
    assert all(item["state"] == "exported" for item in body["data"])

    assert (
        client.get(
            f"{DATA}/invoices", headers=global_key, params={"state": "bogus"}
        ).status_code
        == 400
    )


def test_company_key_sees_only_its_invoices(
    client: TestClient,
    db: Session,
    superuser_token_headers: dict[str, str],
    monkeypatch,
    storage_dir,
):
    monkeypatch.setattr(settings, "EXPORT_API_KEY", None)
    company_a = create_company(db)
    company_b = create_company(db)
    invoice_a = create_invoice(db, InvoiceState.EXPORTED, company=company_a)
    invoice_b = create_invoice(db, InvoiceState.EXPORTED, company=company_b)
    key_headers = issue_company_key(client, superuser_token_headers, company_a)

    listed = client.get(f"{DATA}/invoices", headers=key_headers).json()
    ids = [item["id"] for item in listed["data"]]
    assert ids == [str(invoice_a.id)]

    assert (
        client.get(f"{DATA}/invoices/{invoice_a.id}", headers=key_headers).status_code
        == 200
    )
    assert (
        client.get(f"{DATA}/invoices/{invoice_b.id}", headers=key_headers).status_code
        == 404
    )

    # files follow the same scope: own -> bytes, foreign -> 404
    storage.get_storage().save(str(invoice_a.storage_key), b"%PDF-data")
    storage.get_storage().save(str(invoice_b.storage_key), b"%PDF-other")
    own = client.get(f"{DATA}/invoices/{invoice_a.id}/file", headers=key_headers)
    assert own.status_code == 200
    assert own.content == b"%PDF-data"
    assert (
        client.get(
            f"{DATA}/invoices/{invoice_b.id}/file", headers=key_headers
        ).status_code
        == 404
    )

    # revoking the key removes access entirely (no global key here)
    keys = client.get(
        f"/api/v1/companies/{company_a.id}/api-keys", headers=superuser_token_headers
    ).json()
    assert (
        client.delete(
            f"/api/v1/companies/{company_a.id}/api-keys/{keys[0]['id']}",
            headers=superuser_token_headers,
        ).status_code
        == 200
    )
    assert client.get(f"{DATA}/invoices", headers=key_headers).status_code == 503
