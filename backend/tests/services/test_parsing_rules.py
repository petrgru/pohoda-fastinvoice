"""Rule-based (PDF text layer) extraction that skips the LLM when complete."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.services import parsing

ROOT = Path(__file__).resolve().parents[3]

CZECH_INVOICE = """\
Faktura č. 2026005123
Dodavatel: Acme Trading s.r.o.
Nádražní 15, 602 00 Brno
IČ: 28455001
DIČ: CZ28455001
Datum vydání: 12. 3. 2026
Datum dodání: 12. 3. 2026
Datum splatnosti: 26. 3. 2026

Odběratel: Naše Firma a.s.
IČ: 27195496
DIČ: CZ27195496

Základní materiál                      1 200,00 Kč
Služby                                  300,00 Kč

Celkem bez DPH                         1 500,00 Kč
Sazba DPH 21 %  Základ DPH 1 500,00 Kč  DPH 315,00 Kč
Celkem k úhradě                        1 815,00 Kč

Číslo účtu: 123456789/0600
IBAN: CZ88 0600 0000 0000 0012 3456
"""


def test_rule_extract_czech_invoice() -> None:
    parsed = parsing.rule_extract(CZECH_INVOICE)
    assert parsed is not None
    assert parsed.invoice_number == "2026005123"
    assert parsed.supplier_name == "Acme Trading s.r.o."
    assert parsed.supplier_ico == "28455001"
    assert parsed.supplier_dic == "CZ28455001"
    assert parsed.total_price == pytest.approx(1815.00)
    assert parsed.total_vat == pytest.approx(315.00)
    assert parsed.total_without_vat == pytest.approx(1500.00)
    assert parsed.currency == "CZK"
    assert parsed.issue_date is not None and parsed.issue_date.day == 12
    assert parsed.supplier_bank_account == "123456789/0600"
    assert parsed.supplier_iban == "CZ8806000000000000123456"


def test_rule_extract_prefers_seller_over_buyer_block() -> None:
    """The buyer's IČ (below the "odběratel" label) must not win."""
    parsed = parsing.rule_extract(CZECH_INVOICE)
    assert parsed is not None
    assert parsed.supplier_ico == "28455001"


def test_rule_extract_incomplete_returns_none() -> None:
    assert parsing.rule_extract("Faktura\nmalý doklad bez částek") is None
    # no invoice number -> leave it to the LLM
    text = CZECH_INVOICE.replace("Faktura č. 2026005123", "Daňový doklad")
    assert parsing.rule_extract(text.replace("2026005123", "")) is None


def test_parse_skips_llm_for_complete_text_layer(monkeypatch) -> None:
    calls: list[str] = []

    def fake_call_llm(*args: object, **kwargs: object) -> str:
        calls.append("called")
        raise AssertionError("the LLM must not be consulted")

    monkeypatch.setattr(parsing, "call_llm", fake_call_llm)
    parsed = parsing.parse_invoice_text(CZECH_INVOICE)
    assert parsed is not None
    assert parsed.invoice_number == "2026005123"
    assert calls == []


def test_parse_falls_back_to_rules_when_llm_fails(monkeypatch) -> None:
    import httpx

    def failing(*args: object, **kwargs: object) -> str:
        raise httpx.ConnectError("LLM down")

    monkeypatch.setattr(parsing, "call_llm", failing)
    # the rules can answer this one even though the LLM cannot
    parsed = parsing.parse_invoice_text(CZECH_INVOICE)
    assert parsed is not None
    assert parsed.invoice_number == "2026005123"


def test_merge_missing_fills_llm_gaps() -> None:
    llm = parsing.ParsedInvoice(invoice_number="X-1", total_price="100,00")
    rules = parsing.ParsedInvoice(
        invoice_number="IGNORED",
        supplier_ico="28455001",
        supplier_bank_accounts=[{"account": "123456789/0600", "iban": None}],
    )
    merged = parsing._merge_missing(llm, rules)
    assert merged.invoice_number == "X-1"  # LLM value wins
    assert merged.supplier_ico == "28455001"
    assert merged.supplier_bank_accounts is not None


@pytest.mark.skipif(
    not (ROOT / "test_data/FAKTURA_1333134828.PDF").exists(), reason="sample missing"
)
def test_rule_extract_real_festo_text_layer() -> None:
    data = (ROOT / "test_data/FAKTURA_1333134828.PDF").read_bytes()
    text = parsing.extract_text(data, "application/pdf")
    parsed = parsing.rule_extract(text)
    assert parsed is not None
    assert parsed.invoice_number == "1333134828"
    assert (parsed.supplier_name or "").lower().startswith("festo")
    assert parsed.supplier_ico == "00564737"
    accounts = [row.account for row in parsed.supplier_bank_accounts or []]
    assert "912923/0300" in accounts
    assert parsed.supplier_bank_account == "912923/0300"


def test_rule_extract_fills_vat_items() -> None:
    parsed = parsing.rule_extract(CZECH_INVOICE)
    assert parsed is not None
    assert parsed.vat_items is not None
    rows = [(i.vat_rate, str(i.base_price), str(i.tax_price)) for i in parsed.vat_items]
    assert rows == [("21%", "1500.00", "315.00")]


def test_rule_vat_items_czech_multi_rate() -> None:
    text = (
        "Sazba DPH 12 % Základ DPH 1 000,00 Kč DPH 120,00 Kč\n"
        "Sazba DPH 21,000 % Základ DPH 2 000,00 Kč DPH 420,00 Kč"
    )
    items = parsing.rule_vat_items(text, "CZK", 3540.0)
    assert items is not None
    assert [(i.vat_rate, str(i.base_price)) for i in items] == [
        ("12%", "1000.00"),
        ("21%", "2000.00"),
    ]


def test_rule_vat_items_german_rates() -> None:
    items = parsing.rule_vat_items(
        "19 % MwSt. aus 150,00 EUR = 28,50 EUR", "EUR", 178.5
    )
    assert items is not None
    assert items[0].vat_rate == "19%"
    # the transitional 2020 rate stays legitimate
    items = parsing.rule_vat_items(
        "16 % USt. aus 1 000,00 EUR = 160,00 EUR", "EUR", 1160.0
    )
    assert items is not None
    assert str(items[0].tax_price) == "160.00"


def test_rule_vat_items_zero_rate() -> None:
    items = parsing.rule_vat_items("reverse charge 250,00 EUR", "EUR", 250.0)
    assert items is not None
    assert items[0].vat_rate == "0%"
    assert items[0].tax_price == 0


def test_rule_vat_items_filters_other_currency() -> None:
    text = (
        "Základ daně 60,22 EUR DPH 21% 12,65 EUR\n"
        "Základ daně 1 500,00 Kč DPH 21% 315,00 Kč"
    )
    # lines keep their currency tag; only EUR lines count against the total
    items = parsing.rule_vat_items(text, "EUR", 72.87)
    assert items is not None
    assert sorted(i.currency or "" for i in items) == ["CZK", "EUR"]
    relevant = [i for i in items if i.currency == "EUR"]
    assert sum(i.total_price for i in relevant) == Decimal("72.87")


def test_rule_vat_items_sanity_rejects_mismatch() -> None:
    text = "Sazba DPH 21 % Základ DPH 1 500,00 Kč DPH 315,00 Kč"
    assert parsing.rule_vat_items(text, "CZK", 9999.0) is None
