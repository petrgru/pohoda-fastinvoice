import random

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.services import vat
from tests.utils.invoice import create_invoice

API = "/api/v1/companies"


def test_create_company_requires_auth(client: TestClient):
    response = client.post(API, json={"name": "Firma", "ico": "47123787"})
    assert response.status_code == 401


def test_create_and_get_company(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    import random

    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={
            "name": "Novak Trading a.s.",
            "ico": ico,
            "bank_account": "123456789/0100",
        },
    )
    assert response.status_code == 201
    recipient_company_id = response.json()["id"]
    detail = client.get(
        f"{API}/{recipient_company_id}", headers=superuser_token_headers
    )
    assert detail.status_code == 200
    assert detail.json()["name"] == "Novak Trading a.s."


def test_create_company_invalid_ico(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    for bad_ico in ["123", "123456789", "abcdefgh"]:
        response = client.post(
            API,
            headers=superuser_token_headers,
            json={"name": "F", "ico": bad_ico, "bank_account": "123456789/0100"},
        )
        assert response.status_code == 422


def test_create_company_dic_format_failure(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    for bad_dic in ["CZ12345", "abcd", "CZ12345678901234"]:
        response = client.post(
            API,
            headers=superuser_token_headers,
            json={
                "name": "F",
                "ico": "11111111",
                "dic": bad_dic,
                "bank_account": "123456789/0100",
            },
        )
        assert response.status_code == 422, bad_dic


def test_create_company_dic_invalid_blocks(
    client: TestClient, superuser_token_headers: dict[str, str], monkeypatch
):
    """VIES reports the DIČ as invalid -> saving is blocked (no mod-11)."""
    monkeypatch.setattr(settings, "VATCOMPLY_ENABLED", True)
    monkeypatch.setattr(vat, "_fetch", lambda dic: {"valid": False})
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={
            "name": "F",
            "ico": "11111112",
            "dic": "CZ11111111",
            "bank_account": "123456789/0100",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "errors.vat_invalid"


def test_create_company_dic_service_unreachable_fails_open(
    client: TestClient, superuser_token_headers: dict[str, str], monkeypatch
):
    """When VatComply/VIES is down the company can still be saved."""
    monkeypatch.setattr(settings, "VATCOMPLY_ENABLED", True)

    def boom(dic: str) -> dict:
        raise httpx.ConnectError("vatcomply down")

    monkeypatch.setattr(vat, "_fetch", boom)
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={
            "name": "Offline s.r.o.",
            "ico": ico,
            "dic": "CZ47123737",
            "bank_account": "123456789/0100",
        },
    )
    assert response.status_code == 201, response.text


def test_create_company_with_address(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={
            "name": "Address s.r.o.",
            "ico": ico,
            "address": "Nákladní 3002/2, 74601 Opava",
            "bank_account": "123456789/0100",
        },
    )
    assert response.status_code == 201, response.text
    recipient_company_id = response.json()["id"]
    detail = client.get(
        f"{API}/{recipient_company_id}", headers=superuser_token_headers
    )
    assert detail.json()["address"] == "Nákladní 3002/2, 74601 Opava"


def test_create_company_bank_account_format_failure(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    """Only the account *format* is validated – mod-11 is not required
    (real Czech accounts, e.g. FIO, legitimately do not satisfy it)."""
    for bad_account in ["123456789-0100", "abc/0100", "123456789/12", "CZ12345"]:
        response = client.post(
            API,
            headers=superuser_token_headers,
            json={"name": "F", "ico": "11111112", "bank_account": bad_account},
        )
        assert response.status_code == 422, bad_account


def test_create_company_bank_account_mod11_not_required(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    """A real FIO account (2401384534/2010, fails mod-11) must be accepted."""
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={"name": "FIO s.r.o.", "ico": ico, "bank_account": "2401384534/2010"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["bank_account"] == "2401384534/2010"


def test_create_company_bank_account_iban_normalized(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    """A Czech IBAN is accepted and normalized to the domestic form."""
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={
            "name": "IBAN s.r.o.",
            "ico": ico,
            "bank_account": "CZ88 2010 0000 0024 0138 4534",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["bank_account"] == "2401384534/2010"


def test_create_company_valid_dic_passes(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    import random

    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={
            "name": "Mod11 OK s.r.o.",
            "ico": ico,
            "dic": "CZ47123737",
            "bank_account": "123456789/0100",
        },
    )
    assert response.status_code == 201


def test_create_company_duplicate_ico(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    from tests.utils.invoice import create_company

    existing = create_company(db, name="Existujici")
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={"name": "F", "ico": existing.ico, "bank_account": "123456789/0100"},
    )
    assert response.status_code == 409


def test_list_companies_search(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    from tests.utils.invoice import create_company

    company = create_company(db, name="Siemens Elektronik a.s.")
    response = client.get(API, params={"q": "Siemens"}, headers=superuser_token_headers)
    assert response.status_code == 200
    assert response.json()["count"] >= 1
    response = client.get(
        API, params={"q": company.ico}, headers=superuser_token_headers
    )
    assert response.json()["count"] >= 1


def test_update_company(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    from tests.utils.invoice import create_company

    company = create_company(db)
    response = client.put(
        f"{API}/{company.id}",
        headers=superuser_token_headers,
        json={"name": "Prejmenovana s.r.o.", "is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Prejmenovana s.r.o."
    assert response.json()["is_active"] is False
    # inactive is not in the default list
    listed = client.get(API, headers=superuser_token_headers).json()
    assert company.id not in [c["id"] for c in listed["data"]]


def test_delete_company_with_invoices_conflict(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    from tests.utils.invoice import create_company

    company = create_company(db)
    create_invoice(db, company=company)
    response = client.delete(f"{API}/{company.id}", headers=superuser_token_headers)
    assert response.status_code == 409


def test_delete_free_company(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    from tests.utils.invoice import create_company

    company = create_company(db)
    response = client.delete(f"{API}/{company.id}", headers=superuser_token_headers)
    assert response.status_code == 200


def test_vat_info_returns_registry_data(
    client: TestClient, superuser_token_headers: dict[str, str], monkeypatch
):
    monkeypatch.setattr(settings, "VATCOMPLY_ENABLED", True)
    monkeypatch.setattr(
        vat,
        "_fetch",
        lambda dic: {
            "valid": True,
            "name": "BNET Business s.r.o.",
            "address": "Nákladní 3002/2, Opava",
            "country_code": "CZ",
        },
    )
    response = client.get(
        f"{API}/vat-info", params={"dic": "CZ06846904"}, headers=superuser_token_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["name"] == "BNET Business s.r.o."
    assert body["address"] == "Nákladní 3002/2, Opava"


def test_vat_info_invalid_and_unreachable(
    client: TestClient, superuser_token_headers: dict[str, str], monkeypatch
):
    monkeypatch.setattr(settings, "VATCOMPLY_ENABLED", True)
    # bad format is answered without calling the service
    response = client.get(
        f"{API}/vat-info", params={"dic": "nope"}, headers=superuser_token_headers
    )
    assert response.status_code == 200
    assert response.json()["valid"] is False

    # unreachable service reports 502 so the UI can warn without blocking
    def boom(dic: str) -> dict:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(vat, "_fetch", boom)
    response = client.get(
        f"{API}/vat-info",
        params={"dic": "CZ06846904"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "errors.vat_service_unavailable"


def test_company_vat_update_blocks_invalid(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    from tests.utils.invoice import create_company

    company = create_company(db, name="Vat Update")
    monkeypatch.setattr(settings, "VATCOMPLY_ENABLED", True)
    monkeypatch.setattr(vat, "_fetch", lambda dic: {"valid": False})
    response = client.put(
        f"{API}/{company.id}",
        headers=superuser_token_headers,
        json={"dic": "CZ12345678"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "errors.vat_invalid"


def test_create_company_with_multiple_bank_accounts(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    """Invoices (e.g. from Festo) list several accounts – all must be stored."""
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={
            "name": "Vicectova s.r.o.",
            "ico": ico,
            "bank_account": "23456789/0100",
            "bank_accounts": [
                {"account": "23456789/0100", "iban": "cz65 0300 0000 0000 0091 2923"},
                {
                    "account": "0502/123456789/0100",
                    "iban": "CZ94 0300 1712 80 0091 2923",
                },
                {},  # empty rows are dropped
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert [row["account"] for row in body["bank_accounts"]] == [
        "23456789/0100",
        "0502-123456789/0100",
    ]
    assert body["bank_accounts"][0]["iban"] == "CZ6503000000000000912923"
    detail = client.get(f"{API}/{body['id']}", headers=superuser_token_headers)
    assert len(detail.json()["bank_accounts"]) == 2


def test_update_company_bank_accounts_replaced(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    from tests.utils.invoice import create_company

    company = create_company(db, name="Úty Swap")
    assert company.bank_account  # factory keeps a primary account
    response = client.put(
        f"{API}/{company.id}",
        headers=superuser_token_headers,
        json={
            "bank_accounts": [
                {"account": "987654321/0300", "iban": None},
                {"iban": "CZ2979100000003053600003"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()["bank_accounts"]
    assert len(rows) == 2
    assert rows[1]["iban"] == "CZ2979100000003053600003"
    assert rows[1]["position"] == 1


def test_company_bank_account_invalid_iban(
    client: TestClient, superuser_token_headers: dict[str, str]
):
    ico = str(random.randrange(10000000, 99999999))
    response = client.post(
        API,
        headers=superuser_token_headers,
        json={
            "name": "Špatný IBAN a.s.",
            "ico": ico,
            "bank_account": "123456789/0100",
            "bank_accounts": [{"iban": "NOPE"}],
        },
    )
    assert response.status_code == 422


def test_update_invoice_supplier_iban(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
):
    invoice = create_invoice(db)
    response = client.put(
        f"/api/v1/invoices/{invoice.id}",
        headers=superuser_token_headers,
        json={"supplier_iban": "cz88 0300 0000 0000 0091 2923"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["supplier_iban"] == "CZ8803000000000000912923"


def test_delete_company_cascade_in_develop_mode(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    """DEVELOP=yes deletes the company with all its invoices (even exported)."""

    from app.models import InvoiceState
    from tests.utils.invoice import create_company

    monkeypatch.setattr(settings, "DEVELOP", True)
    company = create_company(db)
    invoices = [
        create_invoice(db, company=company),
        create_invoice(db, InvoiceState.EXPORTED, company=company),
    ]

    invoice_ids = [str(invoice.id) for invoice in invoices]
    response = client.delete(
        f"/api/v1/companies/{company.id}", headers=superuser_token_headers
    )
    assert response.status_code == 200, response.text
    for invoice_id in invoice_ids:
        got = client.get(
            f"/api/v1/invoices/{invoice_id}", headers=superuser_token_headers
        )
        assert got.status_code == 404
    got = client.get(f"/api/v1/companies/{company.id}", headers=superuser_token_headers)
    assert got.status_code == 404


def test_runtime_config_reports_develop_flag(
    client: TestClient, superuser_token_headers: dict[str, str], monkeypatch
):
    monkeypatch.setattr(settings, "DEVELOP", True)
    response = client.get("/api/v1/utils/config/", headers=superuser_token_headers)
    assert response.status_code == 200
    assert response.json() == {"develop": True}
