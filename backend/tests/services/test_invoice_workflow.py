from decimal import Decimal

import pytest
from sqlmodel import Session

from app.models import Invoice, InvoiceState, VatItem, check_vat_items
from app.services import invoices as invoice_service
from app.services.invoices import InvalidTransitionError
from tests.utils.invoice import create_company, create_invoice


def test_change_state_rejects_illegal_transitions(db: Session):
    invoice = create_invoice(db, InvoiceState.NEEDS_REVIEW)
    with pytest.raises(InvalidTransitionError):
        invoice_service.change_state(db, invoice, InvoiceState.EXPORTED)
    with pytest.raises(InvalidTransitionError):
        invoice_service.change_state(db, invoice, InvoiceState.UPLOADED)
    db.rollback()


def test_exported_invoice_never_deletable(db: Session):
    invoice = create_invoice(db, InvoiceState.EXPORTED)
    assert not invoice.can_delete
    with pytest.raises(invoice_service.InvoiceLockedError):
        invoice_service.delete_invoice(db, invoice)
    db.rollback()
    assert db.get(Invoice, invoice.id) is not None


def test_queue_reasons_company_missing_then_pending_approval(db: Session):
    invoice = create_invoice(db, InvoiceState.NEEDS_REVIEW)
    reasons = invoice_service.queue_reasons(db, invoice)
    assert "queue.reason.no_company" in reasons

    company = create_company(db)
    invoice_service.assign_company(db, invoice, company)
    db.commit()
    reasons = invoice_service.queue_reasons(db, invoice)
    assert reasons == ["queue.reason.awaiting_approval"]
    db.rollback()


def test_check_vat_items_variants():
    items = [
        VatItem(
            vat_rate="21%",
            base_price=Decimal("1000"),
            tax_price=Decimal("210"),
            total_price=Decimal("1210"),
        )
    ]
    assert check_vat_items(items, Decimal("1210")) == (True, Decimal("0.00"))
    ok, difference = check_vat_items(items, Decimal("1300"))
    assert ok is False and difference == Decimal("-90.00")
    assert check_vat_items(None, Decimal("100")) == (None, None)


def test_inconsistent_vat_line_flagged():
    items = [
        VatItem(
            vat_rate="21%",
            base_price=Decimal("1000"),
            tax_price=Decimal("200"),
            total_price=Decimal("1300"),  # wrong sum
        )
    ]
    ok, _ = check_vat_items(items, Decimal("1300"))
    assert ok is False


def test_check_vat_items_ignores_foreign_currency_lines():
    items = [
        VatItem(
            vat_rate="21%",
            base_price="60.22",
            tax_price="12.65",
            total_price="72.87",
            currency="EUR",
        ),
        VatItem(
            vat_rate="21%",
            base_price="1500.00",
            tax_price="315.00",
            total_price="1815.00",
            currency="CZK",
        ),
    ]
    ok, difference = check_vat_items(items, Decimal("72.87"), "EUR")
    assert ok is True
    assert difference == Decimal("0.00")
    ok, _ = check_vat_items(items, Decimal("1815.00"), "CZK")
    assert ok is True
    # totals in a currency no line carries – nothing to compare
    assert check_vat_items(items, Decimal("10.00"), "PLN") == (None, None)


def test_vat_rate_warnings_are_informational():
    from app.models import vat_rate_warnings

    items = [
        VatItem(vat_rate="19%", base_price="100", tax_price="19", total_price="119")
    ]
    # the current German rate fits the German supplier – no warning
    assert vat_rate_warnings(items, "DE123456789", None) == []
    # an unusual one only yields a machine key
    odd = [VatItem(vat_rate="25%", base_price="100", tax_price="25", total_price="125")]
    assert vat_rate_warnings(odd, "DE123456789", None) == ["vat.rateUnusual|DE|25%"]
    # historical Czech rates are legitimate
    cz = [VatItem(vat_rate="5%", base_price="100", tax_price="5", total_price="105")]
    assert vat_rate_warnings(cz, "CZ12345678", None) == []
    # verbal rates and unknown countries never warn
    mpo = [VatItem(vat_rate="MPO", base_price="100", tax_price="0", total_price="100")]
    assert vat_rate_warnings(mpo, "GB123456789", None) == []
    assert vat_rate_warnings(None, "CZ12345678", "CZ880100000000001234567890") == []
