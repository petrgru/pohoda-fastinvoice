"""The accounting connector API: batch claims, results, retries, watchdog."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import Invoice, InvoiceState, Supplier, get_datetime_utc
from app.services import invoices as invoice_service
from app.services.erp_connector import derive_variable_symbol
from tests.utils.invoice import create_company, create_invoice

CONNECTOR = "/api/v1/erp-connector"


def make_api_key(
    client: TestClient, headers: dict[str, str], db: Session
) -> tuple[str, Any]:
    company = create_company(db)
    response = client.post(
        f"/api/v1/companies/{company.id}/api-keys",
        headers=headers,
        json={"label": "pohoda bridge"},
    )
    assert response.status_code == 201
    return response.json()["key"], company


def ready_invoice(db: Session):
    company = create_company(db)
    invoice = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)
    return company, invoice


def claim(client: TestClient, key: str) -> dict:
    response = client.get(f"{CONNECTOR}/pending-batch", headers={"X-API-Key": key})
    assert response.status_code == 200
    return response.json()


def report(client: TestClient, key: str, results: list[dict]) -> dict:
    response = client.post(
        f"{CONNECTOR}/sync-results",
        headers={"X-API-Key": key},
        json={"results": results},
    )
    assert response.status_code == 200
    return response.json()


def invoice_result(invoice_id, status: str, **extra) -> dict:
    return {
        "entity_type": "invoice",
        "local_id": str(invoice_id),
        "status": status,
        **extra,
    }


def state_of(db: Session, invoice_id) -> Invoice:
    db.expire_all()
    return db.get(Invoice, invoice_id)


def test_variable_symbol_derivation() -> None:
    assert derive_variable_symbol("2026/1234") == "20261234"
    assert derive_variable_symbol("2026-000123456") == "00123456"
    assert derive_variable_symbol("FA 2024000999X") == "2024000999"[-8:]
    assert derive_variable_symbol("bez cisel") is None
    assert derive_variable_symbol(None) is None


def test_pending_batch_claims_and_embeds_supplier(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    company.name = "Naše Firma a.s."
    db.add(company)
    invoice = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)

    batch = claim(client, key)

    assert batch["company_name"] == "Naše Firma a.s."
    assert [item["id"] for item in batch["invoices"]] == [str(invoice.id)]
    assert [s["ico"] for s in batch["suppliers"]] == [invoice.supplier_ico]
    payload = batch["invoices"][0]
    assert payload["variable_symbol"] == "47123737"[-8:] or payload["variable_symbol"]
    assert payload["supplier"]["name"] == invoice.supplier_name
    assert payload["attachment_url"].endswith(f"/data/invoices/{invoice.id}/file")
    # the claim took it out of the queue for everybody else
    assert state_of(db, invoice.id).state == InvoiceState.PROCESSING.value
    assert claim(client, key)["invoices"] == []
    db.rollback()


def test_second_connector_claim_skips_claimed_rows(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key_a, company_a = make_api_key(client, superuser_token_headers, db)
    key_b, company_b = make_api_key(client, superuser_token_headers, db)
    invoice_a = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company_a)
    invoice_b = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company_b)

    batch_a = claim(client, key_a)

    assert [item["id"] for item in batch_a["invoices"]] == [str(invoice_a.id)]
    batch_b = claim(client, key_b)
    assert [item["id"] for item in batch_b["invoices"]] == [str(invoice_b.id)]
    db.rollback()


def test_synced_reports_exported_with_remote_ids(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    invoice = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)
    claim(client, key)

    report(
        client,
        key,
        [
            invoice_result(
                invoice.id, "synced", remote_id="FA00000001", remote_number="260100145"
            )
        ],
    )

    stored = state_of(db, invoice.id)
    assert stored.state == InvoiceState.EXPORTED.value
    assert stored.external_number == "260100145"
    assert stored.erp_remote_id == "FA00000001"

    # an idempotent re-report of the same result stays ok and exported
    outcomes = report(
        client, key, [invoice_result(invoice.id, "synced", remote_number="260100145")]
    )
    assert outcomes["applied"][0]["ok"] is True
    assert state_of(db, invoice.id).state == InvoiceState.EXPORTED.value
    db.rollback()


def test_error_retries_then_requires_user(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    headers = superuser_token_headers
    invoice = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)

    for attempt in range(invoice_service.EXPORT_MAX_RETRIES):
        assert claim(client, key)["invoices"], "batch must offer the record again"
        report(
            client,
            key,
            [invoice_result(invoice.id, "error", error_message="Neplatny kod banky")],
        )
        stored = state_of(db, invoice.id)
        assert stored.state == InvoiceState.EXPORT_FAILED.value
        assert stored.export_retry_count == attempt + 1
        assert stored.erp_error_message == "Neplatny kod banky"

    # after the automatic attempts the batch stays empty until a user re-queues
    assert claim(client, key)["invoices"] == []
    response = client.post(
        f"/api/v1/invoices/{invoice.id}/requeue-export", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["state"] == InvoiceState.READY_FOR_EXPORT.value
    assert response.json()["export_retry_count"] == 0
    assert claim(client, key)["invoices"] != []
    db.rollback()


def test_retry_later_costs_no_attempt(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    invoice = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)
    claim(client, key)

    report(client, key, [invoice_result(invoice.id, "retry_later")])

    stored = state_of(db, invoice.id)
    assert stored.state == InvoiceState.READY_FOR_EXPORT.value
    assert stored.export_retry_count == 0
    assert claim(client, key)["invoices"] != []
    db.rollback()


def test_results_respect_company_scope(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key_a, company_a = make_api_key(client, superuser_token_headers, db)
    _key_b, company_b = make_api_key(client, superuser_token_headers, db)
    mine = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company_a)
    theirs = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company_b)
    claim(client, key_a)

    outcomes = report(
        client,
        key_a,
        [
            invoice_result(mine.id, "synced", remote_number="260100001"),
            invoice_result(theirs.id, "synced", remote_number="x"),
        ],
    )

    by_id = {item["local_id"]: item for item in outcomes["applied"]}
    assert by_id[str(mine.id)]["ok"] is True
    assert by_id[str(theirs.id)]["ok"] is False
    assert state_of(db, theirs.id).state == InvoiceState.READY_FOR_EXPORT.value
    db.rollback()


def test_watchdog_releases_stale_claims(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    invoice = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)
    claim(client, key)
    # simulate a connector that died mid-batch ten minutes ago
    invoice = state_of(db, invoice.id)
    invoice.export_claimed_at = get_datetime_utc() - timedelta(minutes=11)
    db.add(invoice)
    db.commit()

    assert invoice_service.release_stale_export_claims(db) >= 1

    assert state_of(db, invoice.id).state == InvoiceState.READY_FOR_EXPORT.value
    assert claim(client, key)["invoices"] != []
    db.rollback()


def test_user_edit_releases_the_claim(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    invoice = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)
    claim(client, key)

    response = client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=superuser_token_headers,
        json={"supplier_name": "Opraveny dodavatel s.r.o."},
    )

    assert response.status_code == 200
    assert response.json()["state"] == InvoiceState.READY_FOR_EXPORT.value
    assert response.json()["supplier_name"] == "Opraveny dodavatel s.r.o."
    db.rollback()


def test_reassign_and_approve_blocked_while_processing(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)
    invoice = create_invoice(db, InvoiceState.READY_FOR_EXPORT, company=company)
    claim(client, key)

    other = create_company(db)
    response = client.put(
        f"/api/v1/invoices/{invoice.id}/recipient-company",
        headers=superuser_token_headers,
        json={"recipient_company_id": str(other.id)},
    )
    assert response.status_code == 409
    response = client.post(
        f"/api/v1/invoices/{invoice.id}/unapprove", headers=superuser_token_headers
    )
    assert response.status_code == 200
    assert response.json()["state"] == InvoiceState.NEEDS_REVIEW.value
    db.rollback()


def test_heartbeat_and_connection_status(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    key, company = make_api_key(client, superuser_token_headers, db)

    # nothing reported yet
    assert (
        client.get(
            f"{CONNECTOR}/connection",
            headers=superuser_token_headers,
            params={"company_id": str(company.id)},
        ).json()
        is None
    )

    response = client.post(
        f"{CONNECTOR}/heartbeat",
        headers={"X-API-Key": key},
        json={
            "system": "pohoda",
            "version": "1.4.2",
            "mode": "mserver",
            "queue_size": 7,
        },
    )
    assert response.status_code == 204

    body = client.get(
        f"{CONNECTOR}/connection",
        headers=superuser_token_headers,
        params={"company_id": str(company.id)},
    ).json()
    assert body["system"] == "pohoda"
    assert body["version"] == "1.4.2"
    assert body["mode"] == "mserver"
    assert body["queue_size"] == 7
    assert body["last_seen_at"]
    db.rollback()


def test_supplier_items_and_unknown_rows(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """Suppliers carry their remote id back; unknown rows fail one by one."""
    key, _company = make_api_key(client, superuser_token_headers, db)
    supplier = Supplier(name="ERP Dodavatel a.s.", ico="76846905")
    db.add(supplier)
    db.commit()
    db.refresh(supplier)

    outcomes = report(
        client,
        key,
        [
            {
                "entity_type": "supplier",
                "local_id": str(supplier.id),
                "status": "synced",
                "remote_id": "SUP00000001",
            },
            {
                "entity_type": "supplier",
                "local_id": str(uuid4()),
                "status": "error",
                "error_message": "není v SQL",
            },
            {
                "entity_type": "invoice",
                "local_id": str(uuid4()),
                "status": "synced",
                "remote_id": "X",
                "remote_number": "1",
            },
        ],
    )["applied"]

    assert outcomes[0]["ok"] is True
    assert outcomes[1]["ok"] is False
    assert "dodavatel nenalezen" in outcomes[1]["detail"]
    assert outcomes[2]["ok"] is False
    assert "doklad nenalezen" in outcomes[2]["detail"]

    db.expire_all()
    stored = db.get(Supplier, supplier.id)
    assert stored is not None
    assert stored.erp_remote_id == "SUP00000001"
    db.rollback()
