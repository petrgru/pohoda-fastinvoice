from app.services.parsing import id_candidates

DOC = """Faktura č. 2024000123
Dodavatel: ALFA s.r.o.
IČ: 47123787 DIČ: CZ47123787
Adresa: Praha
Odběratel: BETA a.s.
IČ zákazníka: 28684011 DIČ: CZ28684011
Bankovní spojení: 123456789/0100
IBAN: CZ6501000000000023456789
BIC: KOMBCZPP
Celkem 1210,00 Kč
"""


def test_id_candidates_find_both_parties() -> None:
    result = id_candidates(DOC)
    ico = {item.value: item for item in result["ico"]}
    assert ico["47123787"].entity == "seller"
    assert ico["28684011"].entity == "buyer"
    dic = {item.value: item for item in result["dic"]}
    assert dic["CZ47123787"].entity == "seller"
    assert dic["CZ28684011"].entity == "buyer"
    # seller candidates are offered first
    assert result["ico"][0].value == "47123787"
    assert result["dic"][0].value == "CZ47123787"


def test_id_candidates_skip_iban_and_account() -> None:
    result = id_candidates(DOC)
    values = {item.value for item in result["ico"]} | {
        item.value for item in result["dic"]
    }
    # the 8-digit slice of the IBAN must never surface as an IČ
    assert not any(v in {"01000000", "00000023", "45678900"} for v in values)
    assert "12345678" not in values  # part of the account number row
    dic = {item.value for item in result["dic"]}
    assert not any(v.startswith("CZ65") for v in dic)


def test_id_candidates_mod11_only_ranks() -> None:
    text = "Odběratel: X s.r.o.\nIČ: 12345678\nIČ: 47123788"
    result = id_candidates(text)
    valid = [item for item in result["ico"] if item.looks_valid]
    assert {item.value for item in valid} == {"47123788"}
    # invalid ones are still returned, only ranked later
    assert {item.value for item in result["ico"]} == {"12345678", "47123788"}


def test_id_candidates_empty_text() -> None:
    assert id_candidates("žádná čísla") == {"ico": [], "dic": []}
