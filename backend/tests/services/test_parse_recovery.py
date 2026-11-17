"""A parsed document must never stay stuck in PARSING.

The scanner LLM sometimes extracts a mangled IBAN; that used to crash the
post-parse supplier ledger and leave the invoice in PARSING forever - no
result, no error. Recovery after a restart must also pick such documents up.
"""

import time
from uuid import uuid4

import pytest
from sqlmodel import select

from app.core.config import settings
from app.models import Invoice, InvoiceState, Supplier, normalize_bank_account
from app.services import account_integrity, parsing, storage
from app.services import invoices as invoice_service
from tests.utils.invoice import (
    SAMPLE_PDF_TEXT,
    create_invoice,
    make_pdf_bytes,
    parsed_sample,
)

UPLOAD_URL = "/api/v1/invoices/upload"

# what the vision model made of a scan today: account 187323140/0600 glued
# into an IBAN shape that no validator can accept
GARBAGE_IBAN = "CZ58060000000000187323140"
GOOD_ACCOUNT = "123456789/0100"
ICO = "47123737"


@pytest.fixture
def storage_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", tmp_path)
    return tmp_path


def upload(client, headers) -> dict:
    pdf = make_pdf_bytes(SAMPLE_PDF_TEXT + [f"poznamka-{uuid4().hex}"])
    response = client.post(
        UPLOAD_URL,
        headers=headers,
        files={"file": ("sken.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 201
    detail = client.get(f"/api/v1/invoices/{response.json()['id']}", headers=headers)
    return detail.json()


def mock_parser(monkeypatch, parsed) -> None:
    monkeypatch.setattr(
        parsing, "extract_text", lambda *_args: "\n".join(SAMPLE_PDF_TEXT)
    )
    monkeypatch.setattr(parsing, "parse_invoice_text", lambda *_args, **_kwargs: parsed)


def test_mangled_iban_does_not_stuck_the_invoice(
    client, superuser_token_headers, storage_dir, db, monkeypatch
) -> None:
    parsed = parsed_sample(ico=ICO)
    parsed.supplier_iban = GARBAGE_IBAN  # besides the good main account
    mock_parser(monkeypatch, parsed)

    invoice = upload(client, superuser_token_headers)

    assert invoice["state"] == InvoiceState.NEEDS_REVIEW.value
    assert invoice["parse_error"] is None
    # the mangled value stays on the invoice where the user can correct it
    assert invoice["supplier_iban"] == GARBAGE_IBAN
    # the supplier ledger got the valid account, but not the mangled one
    supplier = db.exec(select(Supplier).where(Supplier.ico == ICO)).one()
    accounts = {row["account"] for row in supplier.bank_accounts}
    assert normalize_bank_account(GOOD_ACCOUNT) in accounts
    assert all(
        GARBAGE_IBAN not in (row["account"], row["iban"])
        for row in supplier.bank_accounts
    )
    db.rollback()


def test_apply_parsed_crash_still_leaves_parsing(
    client, superuser_token_headers, storage_dir, monkeypatch
) -> None:
    mock_parser(monkeypatch, parsed_sample(ico=ICO))

    def boom(*_args, **_kwargs):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(invoice_service, "apply_parsed", boom)

    invoice = upload(client, superuser_token_headers)

    assert invoice["state"] == InvoiceState.NEEDS_REVIEW.value
    assert "Chyba při ukládání výsledku parsování" in invoice["parse_error"]


def test_upsert_skips_malformed_account(db) -> None:
    invoice = create_invoice(
        db,
        InvoiceState.NEEDS_REVIEW,
        supplier_bank_account=GOOD_ACCOUNT,
        supplier_iban=GARBAGE_IBAN,
    )

    supplier = account_integrity.upsert_from_invoice(db, invoice)

    assert supplier is not None
    accounts = {row["account"] for row in supplier.bank_accounts}
    assert normalize_bank_account(GOOD_ACCOUNT) in accounts
    assert all(GARBAGE_IBAN != row["account"] for row in supplier.bank_accounts)
    db.rollback()


def test_recovery_handover_resets_and_queues_stuck(db, monkeypatch) -> None:
    invoice = create_invoice(db, InvoiceState.PARSING)
    processed: list = []
    monkeypatch.setattr(invoice_service, "_process_after_restart", processed.extend)

    count = invoice_service.recover_stuck_invoices()

    assert count >= 1
    deadline = time.monotonic() + 5
    while invoice.id not in processed and time.monotonic() < deadline:
        time.sleep(0.05)
    assert invoice.id in processed
    db.expire_all()
    assert db.get(Invoice, invoice.id).state == InvoiceState.UPLOADED.value
    db.rollback()


def test_recovery_processing_parses_the_document(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", tmp_path)
    invoice = create_invoice(db, InvoiceState.PARSING)
    storage.get_storage().save(invoice.storage_key, make_pdf_bytes(SAMPLE_PDF_TEXT))
    mock_parser(monkeypatch, parsed_sample(ico=ICO))

    invoice_service._process_after_restart([invoice.id])

    deadline = time.monotonic() + 10
    state = None
    while time.monotonic() < deadline:
        db.expire_all()
        state = db.get(Invoice, invoice.id).state
        if state not in (
            InvoiceState.UPLOADED.value,
            InvoiceState.PARSING.value,
        ):
            break
        time.sleep(0.1)
    assert state == InvoiceState.NEEDS_REVIEW.value
    db.rollback()
