from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.models import InvoiceState
from app.services import parsing
from tests.utils.invoice import (
    SAMPLE_PDF_TEXT,
    create_company,
    create_invoice,
    make_pdf_bytes,
    parsed_sample,
)

EXPORT = "/api/v1/export"
EXPORT_KEY = "test-export-key"


@pytest.fixture
def export_key(monkeypatch):
    monkeypatch.setattr(settings, "EXPORT_API_KEY", EXPORT_KEY)
    return {"X-API-Key": EXPORT_KEY}


@pytest.fixture
def storage_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", tmp_path)
    return tmp_path


class FakeParser:
    def __init__(self, monkeypatch, ico: str) -> None:
        self.ico = ico
        monkeypatch.setattr(
            parsing,
            "extract_text",
            lambda data, content_type: "\n".join(SAMPLE_PDF_TEXT),
        )
        monkeypatch.setattr(
            parsing,
            "parse_invoice_text",
            lambda text, image=None: parsed_sample(ico=ico),
        )


@pytest.fixture
def parsing_ok(monkeypatch):
    import random

    return FakeParser(monkeypatch, str(random.randrange(10000000, 99999999)))


def test_export_disabled_without_key(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "EXPORT_API_KEY", None)
    response = client.get(f"{EXPORT}/queue")
    assert response.status_code == 503


def test_export_rejects_missing_or_wrong_key(client: TestClient, export_key):
    assert client.get(f"{EXPORT}/queue").status_code == 401
    assert (
        client.get(f"{EXPORT}/queue", headers={"X-API-Key": "wrong"}).status_code == 401
    )


def test_queue_structure(client: TestClient, export_key):
    response = client.get(f"{EXPORT}/queue", headers=export_key)
    assert response.status_code == 200
    for item in response.json()["data"]:
        assert item["company"] is not None
        assert item["export_url"].endswith("/file")


def test_full_export_flow(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    export_key,
    storage_dir,
    parsing_ok,
):
    # create company with the parsed IČO + upload + auto-match + approve
    company = create_company(db, ico=parsing_ok.ico, name="Fake Parser s.r.o.")
    response = client.post(
        "/api/v1/invoices/upload",
        headers=superuser_token_headers,
        files={
            "file": (
                "faktura.pdf",
                make_pdf_bytes(SAMPLE_PDF_TEXT + ["unique-export-case"]),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201
    invoice_id = response.json()["id"]
    invoice = client.get(
        f"/api/v1/invoices/{invoice_id}", headers=superuser_token_headers
    ).json()
    assert invoice["company"]["id"] == str(company.id)

    approved = client.post(
        f"/api/v1/invoices/{invoice['id']}/approve", headers=superuser_token_headers
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == InvoiceState.READY_FOR_EXPORT.value

    # the connector sees the invoice in its queue
    queue = client.get(f"{EXPORT}/queue", headers=export_key).json()
    ids = [item["id"] for item in queue["data"]]
    assert invoice["id"] in ids
    item = next(i for i in queue["data"] if i["id"] == invoice["id"])
    assert item["company"]["ico"] == parsing_ok.ico
    assert Decimal(item["total_price"]) == Decimal("1210.00")

    # the connector downloads the PDF attachment
    file_response = client.get(
        f"{EXPORT}/invoices/{invoice['id']}/file", headers=export_key
    )
    assert file_response.status_code == 200
    assert file_response.content.startswith(b"%PDF")

    # successful import -> marked as stored, cannot be deleted anymore
    result = client.post(
        f"{EXPORT}/result",
        headers=export_key,
        json={"invoice_id": invoice["id"], "success": True, "batch_ref": "d-2024-001"},
    )
    assert result.status_code == 200

    detail = client.get(
        f"/api/v1/invoices/{invoice['id']}", headers=superuser_token_headers
    ).json()
    assert detail["state"] == InvoiceState.EXPORTED.value
    assert detail["can_delete"] is False

    deletion = client.delete(
        f"/api/v1/invoices/{invoice['id']}", headers=superuser_token_headers
    )
    assert deletion.status_code == 403

    # the invoice leaves the queue, repeated result report is idempotent
    ids = [
        i["id"]
        for i in client.get(f"{EXPORT}/queue", headers=export_key).json()["data"]
    ]
    assert invoice["id"] not in ids
    again = client.post(
        f"{EXPORT}/result",
        headers=export_key,
        json={"invoice_id": invoice["id"], "success": True, "batch_ref": "d-2024-001"},
    )
    assert again.status_code == 200


def test_export_failure_returns_to_review(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    export_key,
):
    company = create_company(db)
    invoice = create_invoice(
        db,
        InvoiceState.NEEDS_REVIEW,
        company=company,
        approved_by=superuser_id(client, superuser_token_headers),
    )
    client.post(
        f"/api/v1/invoices/{invoice.id}/approve", headers=superuser_token_headers
    )
    assert client.get(f"{EXPORT}/queue", headers=export_key).json()["count"] >= 1
    result = client.post(
        f"{EXPORT}/result",
        headers=export_key,
        json={
            "invoice_id": str(invoice.id),
            "success": False,
            "error": "Faktura již existuje v Pohodě",
        },
    )
    assert result.status_code == 200
    detail = client.get(
        f"/api/v1/invoices/{invoice.id}", headers=superuser_token_headers
    ).json()
    assert detail["state"] == InvoiceState.EXPORT_FAILED.value
    assert any(
        r.startswith("queue.reason.export_failed") for r in detail["queue_reasons"]
    )


def superuser_id(client: TestClient, headers: dict[str, str]) -> str:
    return client.get("/api/v1/users/me", headers=headers).json()["id"]


def test_export_result_conflict_for_unapproved(
    client: TestClient, db: Session, export_key
):
    invoice = create_invoice(db, InvoiceState.NEEDS_REVIEW)
    result = client.post(
        f"{EXPORT}/result",
        headers=export_key,
        json={"invoice_id": str(invoice.id), "success": True},
    )
    assert result.status_code == 409


def test_companies_sync_is_gone(client: TestClient, export_key):
    """Bulk company sync was retired – companies are managed in the app."""
    response = client.put(
        f"{EXPORT}/companies/sync",
        headers=export_key,
        json={"companies": [{"name": "X", "ico": "12345678"}]},
    )
    assert response.status_code == 410
