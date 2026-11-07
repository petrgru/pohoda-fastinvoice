import random
from decimal import Decimal
from uuid import uuid4

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

UPLOAD_URL = "/api/v1/invoices/upload"


@pytest.fixture
def storage_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", tmp_path)
    return tmp_path


class FakeParser:
    """Pretends the LLM extracted `parsed_sample(ico)` from every document."""

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
    return FakeParser(monkeypatch, str(random.randrange(10000000, 99999999)))


def unique_pdf() -> bytes:
    return make_pdf_bytes(SAMPLE_PDF_TEXT + [f"poznamka-{uuid4().hex}"])


def upload(client: TestClient, headers: dict[str, str], content: bytes | None = None):
    pdf = content if content is not None else unique_pdf()
    return client.post(
        UPLOAD_URL,
        headers=headers,
        files={"file": ("faktura.pdf", pdf, "application/pdf")},
    )


def test_upload_requires_auth(client: TestClient):
    response = upload(client, {})
    assert response.status_code == 401


def detail(client: TestClient, headers: dict[str, str], invoice_id: str) -> dict:
    response = client.get(f"/api/v1/invoices/{invoice_id}", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_upload_and_parse_flow(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    response = upload(client, superuser_token_headers)
    assert response.status_code == 201
    invoice = detail(client, superuser_token_headers, response.json()["id"])
    assert invoice["state"] == InvoiceState.NEEDS_REVIEW.value
    assert invoice["invoice_number"] == "2024000123"
    assert invoice["supplier_ico"] == parsing_ok.ico
    assert Decimal(invoice["total_price"]) == Decimal("1210.00")
    assert invoice["vat_check_ok"] is True
    assert invoice["company"] is None
    assert "queue.reason.no_company" in invoice["queue_reasons"]
    assert invoice["can_delete"] is True
    assert invoice["llm_confidence"] == 0.92


def test_upload_auto_matches_company(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    storage_dir,
    parsing_ok,
):
    company = create_company(db, ico=parsing_ok.ico, name="Fake Parser s.r.o.")
    response = upload(client, superuser_token_headers)
    assert response.status_code == 201
    body = detail(client, superuser_token_headers, response.json()["id"])
    assert body["company"]["ico"] == company.ico


def test_upload_duplicate_file(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    pdf = unique_pdf()
    first = upload(client, superuser_token_headers, pdf)
    assert first.status_code == 201
    duplicate = upload(client, superuser_token_headers, pdf)
    assert duplicate.status_code == 409


def test_upload_rejects_unsupported_type(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir
):
    response = client.post(
        UPLOAD_URL,
        headers=superuser_token_headers,
        files={"file": ("ucetni.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 415


def test_get_invoice_file(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    response = client.get(
        f"/api/v1/invoices/{created['id']}/file", headers=superuser_token_headers
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_update_fields_and_vat_check(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    url = f"/api/v1/invoices/{created['id']}"

    response = client.put(
        url,
        headers=superuser_token_headers,
        json={"total_price": "1500.00"},
    )
    assert response.status_code == 200
    assert response.json()["vat_check_ok"] is False
    assert Decimal(response.json()["vat_difference"]) == Decimal("-290.00")

    response = client.put(
        url,
        headers=superuser_token_headers,
        json={"total_price": "1210.00"},
    )
    assert response.json()["vat_check_ok"] is True


def test_update_rejects_invalid_ico(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    response = client.put(
        f"/api/v1/invoices/{created['id']}",
        headers=superuser_token_headers,
        json={"supplier_ico": "123"},
    )
    assert response.status_code == 422


def test_fulltext_search(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    response = client.get(
        "/api/v1/invoices",
        params={"q": "TEST SUPPLIER"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert created["id"] in ids

    response = client.get(
        "/api/v1/invoices", params={"q": "2024000123"}, headers=superuser_token_headers
    )
    assert created["id"] in [item["id"] for item in response.json()["data"]]

    # amount (total_price is indexed / cast-searchable)
    response = client.get(
        "/api/v1/invoices", params={"q": "1210"}, headers=superuser_token_headers
    )
    assert created["id"] in [item["id"] for item in response.json()["data"]]

    # partial word from the original document text (OCR)
    response = client.get(
        "/api/v1/invoices", params={"q": "SUPPLIE"}, headers=superuser_token_headers
    )
    assert created["id"] in [item["id"] for item in response.json()["data"]]

    response = client.get(
        "/api/v1/invoices",
        params={"q": "neexistujicipridavek"},
        headers=superuser_token_headers,
    )
    assert response.json()["count"] == 0


def test_approve_requires_company(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    storage_dir,
    parsing_ok,
):
    created = upload(client, superuser_token_headers).json()
    response = client.post(
        f"/api/v1/invoices/{created['id']}/approve", headers=superuser_token_headers
    )
    assert response.status_code == 409

    company = create_company(db)
    response = client.put(
        f"/api/v1/invoices/{created['id']}/recipient-company",
        headers=superuser_token_headers,
        json={"recipient_company_id": str(company.id)},
    )
    assert response.status_code == 200

    response = client.post(
        f"/api/v1/invoices/{created['id']}/approve", headers=superuser_token_headers
    )
    assert response.status_code == 200
    assert response.json()["state"] == InvoiceState.READY_FOR_EXPORT.value
    assert response.json()["queue_reasons"] == []


def test_assign_missing_company_404(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    import uuid

    response = client.put(
        f"/api/v1/invoices/{created['id']}/recipient-company",
        headers=superuser_token_headers,
        json={"recipient_company_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


def test_approve_blocked_on_vat_mismatch(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    storage_dir,
    parsing_ok,
):
    company = create_company(db)
    created = upload(client, superuser_token_headers).json()
    response = client.put(
        f"/api/v1/invoices/{created['id']}/recipient-company",
        headers=superuser_token_headers,
        json={"recipient_company_id": str(company.id)},
    )
    assert response.status_code == 200
    response = client.put(
        f"/api/v1/invoices/{created['id']}",
        headers=superuser_token_headers,
        json={"total_price": "999.00"},
    )
    assert response.json()["vat_check_ok"] is False
    response = client.post(
        f"/api/v1/invoices/{created['id']}/approve", headers=superuser_token_headers
    )
    assert response.status_code == 409


def test_queue_lists_pending_invoices(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    response = client.get("/api/v1/invoices/queue", headers=superuser_token_headers)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert created["id"] in ids


def test_delete_allowed_before_export(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    response = client.delete(
        f"/api/v1/invoices/{created['id']}", headers=superuser_token_headers
    )
    assert response.status_code == 200
    response = client.get(
        f"/api/v1/invoices/{created['id']}", headers=superuser_token_headers
    )
    assert response.status_code == 404


def test_exported_invoice_is_immutable(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    invoice = create_invoice(db, InvoiceState.EXPORTED)
    url = f"/api/v1/invoices/{invoice.id}"
    assert client.delete(url, headers=superuser_token_headers).status_code == 403
    assert (
        client.put(
            url, headers=superuser_token_headers, json={"total_price": "1.00"}
        ).status_code
        == 403
    )
    assert (
        client.post(f"{url}/unapprove", headers=superuser_token_headers).status_code
        == 403
    )


def test_reparse(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    response = client.post(
        f"/api/v1/invoices/{created['id']}/reparse", headers=superuser_token_headers
    )
    assert response.status_code == 200
    detail = client.get(
        f"/api/v1/invoices/{created['id']}", headers=superuser_token_headers
    )
    assert detail.json()["state"] == InvoiceState.NEEDS_REVIEW.value


def test_create_company_for_invoice(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        f"/api/v1/invoices/{created['id']}/recipient-company/create",
        headers=superuser_token_headers,
        json={
            "name": "Nová Partnerská s.r.o.",
            "ico": ico,
            "bank_account": "123456789/0100",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["company"]["ico"] == ico
    assert body["company"]["source"] == "manual"
    assert body["queue_reasons"] == ["queue.reason.awaiting_approval"]


def test_create_company_for_invoice_without_bank_account(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    """From-document companies rarely know their own account – saving is OK."""
    created = upload(client, superuser_token_headers).json()
    response = client.post(
        f"/api/v1/invoices/{created['id']}/recipient-company/create",
        headers=superuser_token_headers,
        json={
            "name": "Bez Účtu s.r.o.",
            "ico": str(random.randrange(10000000, 99999999)),
        },
    )
    assert response.status_code == 201
    assert response.json()["company"]["bank_account"] is None


def test_create_company_for_invoice_duplicate_ico(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    storage_dir,
    parsing_ok,
):
    from tests.utils.invoice import create_company

    existing = create_company(db)
    created = upload(client, superuser_token_headers).json()
    before = client.get("/api/v1/companies", headers=superuser_token_headers).json()[
        "count"
    ]
    response = client.post(
        f"/api/v1/invoices/{created['id']}/recipient-company/create",
        headers=superuser_token_headers,
        json={"name": "X", "ico": existing.ico, "bank_account": "123456789/0100"},
    )
    assert response.status_code == 409
    after = client.get("/api/v1/companies", headers=superuser_token_headers).json()[
        "count"
    ]
    assert after == before
    detail = client.get(
        f"/api/v1/invoices/{created['id']}", headers=superuser_token_headers
    ).json()
    assert detail["company"] is None


def test_create_company_for_invoice_invalid_ico(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    created = upload(client, superuser_token_headers).json()
    response = client.post(
        f"/api/v1/invoices/{created['id']}/recipient-company/create",
        headers=superuser_token_headers,
        json={"name": "X", "ico": "bad", "bank_account": "123456789/0100"},
    )
    assert response.status_code == 422


def test_create_company_for_invoice_exported_forbidden(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    invoice = create_invoice(db, InvoiceState.EXPORTED)
    response = client.post(
        f"/api/v1/invoices/{invoice.id}/recipient-company/create",
        headers=superuser_token_headers,
        json={
            "name": "X",
            "ico": str(random.randrange(10000000, 99999999)),
            "bank_account": "123456789/0100",
        },
    )
    assert response.status_code == 403


def test_upload_duplicate_rejected(
    client: TestClient, superuser_token_headers: dict[str, str], storage_dir, parsing_ok
):
    """The same bytes can never be stored twice – the second upload is a 409."""
    content = unique_pdf()
    first = upload(client, superuser_token_headers, content)
    assert first.status_code == 201
    # identical content under a different filename is still the same document
    second = client.post(
        UPLOAD_URL,
        headers=superuser_token_headers,
        files={"file": ("jiny-nazev.pdf", content, "application/pdf")},
    )
    assert second.status_code == 409
    assert second.json()["detail"].startswith("errors.duplicate_file")
    # no second file leaked to storage: content-addressed key is shared
    stored = [p for p in storage_dir.rglob("*") if p.is_file()]
    assert len(stored) == 1


def test_create_company_bank_account_is_optional(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    """The account used to be required; it can be filled in later now."""
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        "/api/v1/companies",
        headers=superuser_token_headers,
        json={"name": "Bez Učtu s.r.o.", "ico": ico},
    )
    assert response.status_code == 201
    assert response.json()["bank_account"] is None


def test_bank_account_with_prefix(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    """Account with prefix (0502-...) passes the official mod-11 and is
    normalized to the canonical prefix-number/bank form."""
    import random as _random

    ico = str(_random.randrange(10000000, 99999999))
    # 0502 + 123456783/0100: mod-11 over '000502' + '123456783' = 176 (÷11)
    response = client.post(
        "/api/v1/companies",
        headers=superuser_token_headers,
        json={
            "name": "Predcisli s.r.o.",
            "ico": ico,
            "bank_account": "0502/123456783/0100",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["bank_account"] == "0502-123456783/0100"


def test_bank_account_invalid_format(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        "/api/v1/companies",
        headers=superuser_token_headers,
        json={"name": "Špatně s.r.o.", "ico": ico, "bank_account": "123456789-0100"},
    )
    assert response.status_code == 422


def test_update_invoice_supplier_bank_accounts(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    """The parsed multi-account list is stored, normalized and returned for
    company-dialog prefill (invoices from Festo list several accounts)."""
    invoice = create_invoice(db)
    response = client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=superuser_token_headers,
        json={
            "supplier_bank_accounts": [
                {"account": "912923/0300", "iban": "cz88 0300 0000 0000 0091 2923"},
                {"account": None, "iban": "CZ9403001712800000912923"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()["supplier_bank_accounts"]
    assert rows[0]["iban"] == "CZ8803000000000000912923"
    fetched = client.get(
        f"/api/v1/invoices/{invoice.id}", headers=superuser_token_headers
    )
    assert len(fetched.json()["supplier_bank_accounts"]) == 2


def test_update_invoice_bank_account_invalid_iban(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    invoice = create_invoice(db)
    response = client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=superuser_token_headers,
        json={"supplier_bank_accounts": [{"iban": "NOPE!!"}]},
    )
    assert response.status_code == 422


def test_locked_while_parsing(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    """Fields stay frozen while the LLM is still working on the document."""
    from app.models import Invoice

    invoice = create_invoice(db)
    record = db.get(Invoice, invoice.id)
    record.state = InvoiceState.PARSING
    db.add(record)
    db.commit()

    response = client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=superuser_token_headers,
        json={"supplier_name": "Brzy hotová"},
    )
    assert response.status_code == 409
    assert "errors.invoice_parsing" in response.json()["detail"]

    company = create_company(db)
    response = client.put(
        f"/api/v1/invoices/{invoice.id}/recipient-company",
        headers=superuser_token_headers,
        json={"recipient_company_id": str(company.id)},
    )
    assert response.status_code == 409


def test_search_uses_meilisearch_when_enabled(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core.config import settings
    from app.services import search_index

    invoice = create_invoice(db)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"hits": [{"id": str(invoice.id)}], "estimatedTotalHits": 1}

    def fake_request(method, path, **kw):  # noqa: ANN001, ARG001
        return FakeResponse()

    monkeypatch.setattr(settings, "MEILISEARCH_ENABLED", True)
    monkeypatch.setattr(search_index, "_index_ready", False)
    monkeypatch.setattr(search_index, "_request", fake_request)
    response = client.get(
        "/api/v1/invoices", params={"q": "cokoliv"}, headers=superuser_token_headers
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["id"] == str(invoice.id)


def test_search_falls_back_when_meilisearch_down(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core.config import settings
    from app.services import search_index

    invoice = create_invoice(db)
    monkeypatch.setattr(settings, "MEILISEARCH_ENABLED", True)
    monkeypatch.setattr(search_index, "_index_ready", False)
    monkeypatch.setattr(search_index, "_request", lambda *a, **k: None)
    response = client.get(
        "/api/v1/invoices",
        params={"q": invoice.invoice_number},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert any(item["id"] == str(invoice.id) for item in response.json()["data"])


def test_search_falls_back_on_empty_meilisearch_index(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    """An empty/stale Meilisearch index must not hide invoices from the DB."""
    from app.core.config import settings
    from app.services import search_index

    invoice = create_invoice(db)

    class EmptyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"hits": [], "estimatedTotalHits": 0}

    monkeypatch.setattr(settings, "MEILISEARCH_ENABLED", True)
    monkeypatch.setattr(search_index, "_index_ready", False)
    monkeypatch.setattr(search_index, "_request", lambda *a, **k: EmptyResponse())
    response = client.get(
        "/api/v1/invoices",
        params={"q": invoice.invoice_number},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert any(item["id"] == str(invoice.id) for item in response.json()["data"])


def test_invoice_stats_buckets(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    """The dashboard aggregates by day/week/month with a state breakdown."""
    from datetime import timedelta

    from app.services.invoices import get_datetime_utc

    today = get_datetime_utc().date()
    create_invoice(db, InvoiceState.EXPORTED, issue_date=today)
    create_invoice(db, InvoiceState.NEEDS_REVIEW, issue_date=today)
    create_invoice(db, InvoiceState.NEEDS_REVIEW, issue_date=today - timedelta(days=45))

    response = client.get("/api/v1/invoices/stats", headers=superuser_token_headers)
    assert response.status_code == 200
    data = response.json()

    day = {bucket["period"]: bucket for bucket in data["day"]}
    assert day[today.isoformat()]["by_state"]["exported"] >= 1
    assert day[today.isoformat()]["by_state"]["needs_review"] >= 1
    assert len(data["day"]) == 30
    assert len(data["week"]) == 12
    assert len(data["month"]) == 12
    # the 45-days-old invoice lands in one of the month buckets
    assert sum(b["by_state"]["needs_review"] for b in data["month"]) >= 2
    assert data["totals"]["exported"] >= 1
    # every period appears exactly once
    assert len({bucket["period"] for bucket in data["month"]}) == 12
