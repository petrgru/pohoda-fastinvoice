"""Duplicate detection: same document number from the same supplier.

Scanning the same paper invoice twice produces different PDF bytes every
time, so the upload-time file hash cannot be the only guard – the business
key (normalized number + supplier) must block approving a second copy.
"""

import random
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import InvoiceState
from tests.utils.invoice import create_company, create_invoice

ICO = "27567575"


def unique_number() -> str:
    # the test database is shared for the whole session and the factories
    # commit – a fixed number would collide with copies leaked by other tests
    return f"FV{random.randrange(1_000_000_000, 9_999_999_999)}"


def pair(db: Session, **overrides: Any) -> tuple[Any, Any]:
    """Two copies of one document, both parsed and waiting for review."""
    company = create_company(db)
    data = {"invoice_number": unique_number(), "supplier_ico": ICO}
    data.update(overrides)
    first = create_invoice(db, InvoiceState.NEEDS_REVIEW, company=company, **data)
    second = create_invoice(db, InvoiceState.NEEDS_REVIEW, company=company, **data)
    return first, second


def approve(
    client: TestClient,
    headers: dict[str, str],
    invoice_id: Any,
    body: dict | None = None,
):
    return client.post(
        f"/api/v1/invoices/{invoice_id}/approve", headers=headers, json=body
    )


def test_each_copy_warns_about_the_other(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    first, second = pair(db)

    body = client.get(
        f"/api/v1/invoices/{second.id}", headers=superuser_token_headers
    ).json()
    assert [item["id"] for item in body["duplicates"]] == [str(first.id)]
    assert body["duplicates"][0]["approved"] is False
    assert any(
        reason.startswith("queue.reason.duplicate|") for reason in body["queue_reasons"]
    )
    # the warning points both ways
    body = client.get(
        f"/api/v1/invoices/{first.id}", headers=superuser_token_headers
    ).json()
    assert [item["id"] for item in body["duplicates"]] == [str(second.id)]
    db.rollback()


def test_approving_the_second_copy_is_blocked_until_the_user_decides(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    first, second = pair(db)
    assert approve(client, superuser_token_headers, first.id).status_code == 200, (
        "the first copy is not a duplicate of anything"
    )

    blocked = approve(client, superuser_token_headers, second.id)
    assert blocked.status_code == 409
    assert "errors.duplicate_approved" in blocked.json()["detail"]

    # explicit decision: keep this copy, drop the approved original
    ok = approve(
        client, superuser_token_headers, second.id, {"delete_duplicates": True}
    )
    assert ok.status_code == 200
    assert ok.json()["state"] == InvoiceState.READY_FOR_EXPORT.value
    assert (
        client.get(f"/api/v1/invoices/{first.id}", headers=superuser_token_headers)
    ).status_code == 404
    assert (
        approve(
            client, superuser_token_headers, second.id, {"delete_duplicates": True}
        ).status_code
        == 200
    )
    db.rollback()


def test_approved_but_unconfirmed_duplicate_does_not_block(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    # both copies are still only "needs review" – approving one is a normal
    # decision, the UI just shows the warning from the test above
    _first, second = pair(db)
    assert approve(client, superuser_token_headers, second.id).status_code == 200
    db.rollback()


def test_exported_original_cannot_be_replaced(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    number = unique_number()
    company = create_company(db)
    original = create_invoice(
        db,
        InvoiceState.EXPORTED,
        company=company,
        invoice_number=number,
        supplier_ico=ICO,
    )
    copy = create_invoice(
        db,
        InvoiceState.NEEDS_REVIEW,
        company=company,
        invoice_number=number,
        supplier_ico=ICO,
    )

    blocked = approve(
        client, superuser_token_headers, copy.id, {"delete_duplicates": True}
    )
    assert blocked.status_code == 409
    assert "errors.duplicate_of_exported" in blocked.json()["detail"]
    assert (
        client.get(f"/api/v1/invoices/{original.id}", headers=superuser_token_headers)
    ).status_code == 200
    db.rollback()


def test_separators_and_case_do_not_matter(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    company = create_company(db)
    digits = str(random.randrange(1_000_000_000, 9_999_999_999))
    create_invoice(
        db,
        InvoiceState.READY_FOR_EXPORT,
        company=company,
        invoice_number=f"FV {digits[:4]}-{digits[4:]}",
        supplier_ico=ICO,
    )
    copy = create_invoice(
        db,
        InvoiceState.NEEDS_REVIEW,
        company=company,
        invoice_number=f"fv/{digits}",
        supplier_ico=ICO,
    )

    blocked = approve(client, superuser_token_headers, copy.id)
    assert blocked.status_code == 409
    assert "errors.duplicate_approved" in blocked.json()["detail"]
    db.rollback()


def test_same_number_from_a_different_supplier_is_no_duplicate(db: Session) -> None:
    from app.services import invoices as invoice_service

    number = unique_number()

    company = create_company(db)
    create_invoice(
        db,
        InvoiceState.READY_FOR_EXPORT,
        company=company,
        invoice_number=number,
        supplier_ico=ICO,
    )
    other = create_invoice(
        db,
        InvoiceState.NEEDS_REVIEW,
        company=company,
        invoice_number=number,
        supplier_ico="12345678",
    )
    assert invoice_service.find_duplicates(db, other) == []


def test_supplier_name_matches_when_ico_is_missing(db: Session) -> None:
    from app.services import invoices as invoice_service

    number = unique_number()

    company = create_company(db)
    create_invoice(
        db,
        InvoiceState.READY_FOR_EXPORT,
        company=company,
        invoice_number=number,
        supplier_ico=None,
        supplier_name="ACME, s.r.o.",
    )
    copy = create_invoice(
        db,
        InvoiceState.NEEDS_REVIEW,
        company=company,
        invoice_number=number,
        supplier_ico=None,
        supplier_name="Acme",
    )
    assert len(invoice_service.find_duplicates(db, copy)) == 1


def test_duplicates_are_found_across_recipient_companies(db: Session) -> None:
    from app.services import invoices as invoice_service

    number = unique_number()

    create_invoice(
        db,
        InvoiceState.READY_FOR_EXPORT,
        company=create_company(db),
        invoice_number=number,
        supplier_ico=ICO,
    )
    in_other_company = create_invoice(
        db,
        InvoiceState.NEEDS_REVIEW,
        company=create_company(db),
        invoice_number=number,
        supplier_ico=ICO,
    )
    assert len(invoice_service.find_duplicates(db, in_other_company)) == 1
