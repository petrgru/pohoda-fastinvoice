"""Extraction of multiple supplier payment accounts (e.g. invoices from Festo)."""

from app.models import ParsedInvoice
from app.services import parsing


def test_multiple_accounts_normalized_and_primary_derived() -> None:
    parsed = ParsedInvoice(
        invoice_number="1333134828",
        supplier_bank_accounts=[
            {"account": "912923/0300", "iban": "cz88 0300 0000 0000 0091 2923"},
            {"account": "01292280/0300", "iban": "CZ94 0300 1712 8000 0091 2923"},
        ],
    )
    out = parsing.normalize_parsed(parsed)
    assert [row.account for row in out.supplier_bank_accounts] == [  # type: ignore[union-attr]
        "912923/0300",
        "01292280/0300",
    ]
    assert out.supplier_bank_accounts[0].iban == "CZ8803000000000000912923"  # type: ignore[index]
    # single-value fields get the main (first) account for the Pohoda export
    assert out.supplier_bank_account == "912923/0300"
    assert out.supplier_iban == "CZ8803000000000000912923"


def test_explicit_primary_fields_are_not_overwritten() -> None:
    parsed = ParsedInvoice(
        supplier_bank_account="123456789/0100",
        supplier_bank_accounts=[{"account": "912923/0300", "iban": None}],
    )
    out = parsing.normalize_parsed(parsed)
    assert out.supplier_bank_account == "123456789/0100"


def test_empty_account_rows_and_iban_format() -> None:
    parsed = ParsedInvoice(
        supplier_bank_accounts=[{"account": "0502/123456789/0100", "iban": None}]
    )
    out = parsing.normalize_parsed(parsed)
    assert out.supplier_bank_accounts[0].account == "0502-123456789/0100"  # type: ignore[index]
    assert out.supplier_bank_account == "0502-123456789/0100"
