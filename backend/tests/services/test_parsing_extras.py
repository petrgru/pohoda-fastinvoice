from datetime import date

from app.services import parsing

DOC = """Faktura č. 2024000777
ALFA s.r.o.
Nádražní 9, 602 00 Brno
info@alfa.example
IČ: 47123737 DIČ: CZ47123737
Objednatel: BETA a.s.
kontakt@beta.example
Datum vystavení: 1.3.2024
Datum zdanitelného plnění: 15.2.2024
Datum splatnosti: 15.3.2024
Splatnost: 14 dní
Celkem 1210,00 Kč
"""


def test_rule_payment_variants() -> None:
    assert parsing.rule_payment("Datum splatnosti: 15.3.2024") == "splatna"
    assert parsing.rule_payment("Faktura byla uhrazena") == "zaplacena"
    assert parsing.rule_payment("Zaplaceno dne 5.3.2024") == "zaplacena"
    assert parsing.rule_payment("Úhrada příkazem k inkasu") == "inkasem"
    assert parsing.rule_payment("Způsob úhrady: hotově") == "hotove"
    assert parsing.rule_payment("bez jakéhokoliv údaje") is None


def test_rule_supplier_email_skips_buyer() -> None:
    assert parsing.rule_supplier_email(DOC) == "info@alfa.example"


def test_rule_extract_duzp_and_payment() -> None:
    parsed = parsing.rule_extract(DOC + " " + "x" * 80)
    assert parsed is not None
    assert parsed.issue_date == date(2024, 3, 1)
    assert parsed.delivery_date == date(2024, 2, 15)
    assert parsed.due_date == date(2024, 3, 15)
    assert parsed.payment_method == "splatna"
    assert parsed.supplier_email == "info@alfa.example"
    # no explicit control-reporting date → it follows the tax supply date
    assert parsed.kh_date == date(2024, 2, 15)


def test_duzp_falls_back_to_issue_date() -> None:
    text = (
        "Faktura 2024000777 ALFA s.r.o. IČ: 47123737 DIČ: CZ47123737 "
        "Datum vystavení: 1.3.2024 Datum splatnosti: 15.3.2024 Celkem 1210,00 Kč "
        + "filler " * 20
    )
    parsed = parsing.rule_extract(text)
    assert parsed is not None
    assert parsed.delivery_date == date(2024, 3, 1)  # = issue date
    assert parsed.kh_date == date(2024, 3, 1)
    assert parsed.payment_method == "splatna"


def test_normalize_fallback_chain() -> None:
    parsed = parsing.ParsedInvoice(issue_date=date(2024, 5, 1))
    parsing.normalize_parsed(parsed)
    assert parsed.delivery_date == date(2024, 5, 1)
    assert parsed.kh_date == date(2024, 5, 1)
