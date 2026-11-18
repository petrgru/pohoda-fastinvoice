"""Registry verification: IČO pre-check, provider parsing, gating, backoff."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlmodel import Session

from app.core.config import settings
from app.models import (
    Invoice,
    InvoiceState,
    Supplier,
    SupplierRegistryStatus,
    get_datetime_utc,
)
from app.services import invoices as invoice_service
from app.services import registry
from tests.utils.invoice import create_company

VALID_ICO = "06846904"  # real, mod-11 clean
INVALID_ICO = "11111111"


def make_ico() -> str:
    """A fresh mod-11 clean IČO (the supplier table keeps ico unique)."""
    import random

    while True:
        base = str(random.randrange(1_000_000, 9_999_999))
        total = sum(
            int(digit) * weight
            for digit, weight in zip(base, (8, 7, 6, 5, 4, 3, 2), strict=True)
        )
        check = (-total) % 11
        if check < 10:
            return base + str(check)


ARES_RECORD = {
    "vystup": {
        "HS": {
            "ID": {"CIS_ICO": VALID_ICO, "DIC": "CZ" + VALID_ICO},
            "PREDS": {"obchodni_jmeno": "ACME s.r.o."},
            "ADR1H": {"sidlo": {"obec": "Praha", "psc": "15000", "ulice": "Nádražní"}},
            "PRIZNAKY": {"platce_dph_zakonny": "A", "datum_upadku": ""},
        }
    }
}

ARES_RECORD_INSOLVENT = {
    "HS": {
        "PREDS": {"obchodni_jmeno": "KRACH s.r.o."},
        "PRIZNAKY": {"datum_upadku": "2026-05-17"},
    }
}


def test_ico_checksum():
    assert registry.ico_checksum_ok(VALID_ICO)
    assert not registry.ico_checksum_ok(INVALID_ICO)
    assert not registry.ico_checksum_ok("1234567")
    assert not registry.ico_checksum_ok("1234567a")
    assert not registry.ico_checksum_ok(None)


def test_next_backoff_ladder():
    assert registry.next_backoff(1) == timedelta(minutes=10)
    assert registry.next_backoff(2) == timedelta(hours=1)
    assert registry.next_backoff(3) == timedelta(hours=6)
    assert registry.next_backoff(4) == timedelta(hours=24)
    assert registry.next_backoff(99) == timedelta(hours=24)


def test_export_block_reason_matrix():
    def sup(status: str, *, insolvency: bool = False, released: bool = False) -> Any:
        return Supplier(
            name="X",
            ico=VALID_ICO,
            registry_status=status,
            in_insolvency=insolvency,
            export_confirmed_manually=released,
        )

    assert registry.export_block_reason(None) is None
    assert (
        registry.export_block_reason(sup(SupplierRegistryStatus.PENDING.value))
        == "registry_pending"
    )
    assert (
        registry.export_block_reason(sup(SupplierRegistryStatus.NOT_FOUND.value))
        == "registry_not_found"
    )
    assert (
        registry.export_block_reason(sup(SupplierRegistryStatus.VERIFIED.value)) is None
    )
    assert (
        registry.export_block_reason(sup(SupplierRegistryStatus.NOT_CHECKABLE.value))
        is None
    )
    blocked = sup(SupplierRegistryStatus.VERIFIED.value, insolvency=True)
    assert registry.export_block_reason(blocked) == "supplier_insolvency"
    assert registry.export_allowed(blocked) is False
    assert (
        registry.export_allowed(
            sup(
                SupplierRegistryStatus.VERIFIED.value,
                insolvency=True,
                released=True,
            )
        )
        is True
    )


def test_ares_parser_reads_identification_and_flags():
    record = registry.AresProvider()._parse(ARES_RECORD)
    assert record is not None
    assert record.name == "ACME s.r.o."
    assert "Praha" in (record.address or "")
    assert record.dic == "CZ" + VALID_ICO
    assert record.vat_payer is True
    assert record.in_insolvency is False


def test_ares_parser_detects_insolvency():
    record = registry.AresProvider()._parse(ARES_RECORD_INSOLVENT)
    assert record is not None
    assert record.in_insolvency is True
    assert record.insolvency_info == {"source": "ares", "detail": "2026-05-17"}


def test_ares_parser_empty_answer_is_not_a_subject():
    assert registry.AresProvider()._parse({"vystup": {"chyba": "neexistuje"}}) is None


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", settings.ARES_REST_URL))


def test_ares_404_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(registry.httpx, "get", lambda *a, **k: _response(404))
    assert registry.AresProvider().lookup_ico(VALID_ICO) is None


def test_ares_5xx_raises_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(registry.httpx, "get", lambda *a, **k: _response(503))
    with pytest.raises(registry.RegistryUnavailableError):
        registry.AresProvider().lookup_ico(VALID_ICO)


def _supplier(**kwargs: Any) -> Supplier:
    defaults: dict[str, Any] = {"name": "Test s.r.o.", "ico": VALID_ICO}
    defaults.update(kwargs)
    return Supplier(**defaults)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "REGISTRY_ENABLED", True)
    monkeypatch.setattr(settings, "REGISTRY_MIN_INTERVAL_SECONDS", 0.0)


def test_refresh_verified_flow(enabled: None, monkeypatch: pytest.MonkeyPatch):
    record = registry.RegistryRecord(
        name="ACME s.r.o.",
        address="Praha",
        dic="CZ" + VALID_ICO,
        vat_payer=True,
        in_insolvency=False,
    )
    monkeypatch.setattr(registry.AresProvider, "lookup_ico", lambda self, ico: record)
    supplier = _supplier(registry_status=SupplierRegistryStatus.PENDING.value)
    assert registry.refresh_supplier(supplier) is True
    assert supplier.registry_status == SupplierRegistryStatus.VERIFIED.value
    assert supplier.registry_data["source"] == "ares"
    assert supplier.registry_next_check_at is not None
    assert supplier.registry_next_check_at > get_datetime_utc() + timedelta(days=6)


def test_refresh_not_found_retries_next_day(
    enabled: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(registry.AresProvider, "lookup_ico", lambda self, ico: None)
    supplier = _supplier(registry_status=SupplierRegistryStatus.PENDING.value)
    registry.refresh_supplier(supplier)
    assert supplier.registry_status == SupplierRegistryStatus.NOT_FOUND.value
    horizon = get_datetime_utc()
    assert supplier.registry_next_check_at <= horizon + timedelta(hours=25)
    assert supplier.registry_next_check_at >= horizon + timedelta(hours=23)


def test_refresh_invalid_ico_never_queries(
    enabled: None, monkeypatch: pytest.MonkeyPatch
):
    def no_query(self: Any, ico: str) -> Any:
        raise AssertionError("must not query with a bad IČO")

    monkeypatch.setattr(registry.AresProvider, "lookup_ico", no_query)
    supplier = _supplier(ico=INVALID_ICO)
    registry.refresh_supplier(supplier)
    assert supplier.registry_status == SupplierRegistryStatus.ICO_INVALID.value
    assert supplier.registry_next_check_at is None


def test_schedule_retry_walks_the_ladder():
    supplier = _supplier(registry_status=SupplierRegistryStatus.PENDING.value)
    registry.schedule_retry(supplier)
    assert supplier.registry_check_attempts == 1
    first = supplier.registry_next_check_at
    assert first is not None
    registry.schedule_retry(supplier)
    assert supplier.registry_check_attempts == 2
    assert supplier.registry_next_check_at > first + timedelta(minutes=30)


def test_refresh_without_identifiers_is_not_checkable(enabled: None):
    supplier = Supplier(name="Jan Novák", ico=None, dic=None)
    assert registry.refresh_supplier(supplier) is True
    assert supplier.registry_status == SupplierRegistryStatus.NOT_CHECKABLE.value


def test_initial_status_requires_registry_enabled(enabled):
    supplier = _supplier()
    assert registry.initial_status(supplier) == SupplierRegistryStatus.PENDING.value
    supplier.registry_status = SupplierRegistryStatus.NOT_CHECKABLE.value
    assert (
        registry.initial_status(_supplier(ico=INVALID_ICO))
        == SupplierRegistryStatus.ICO_INVALID.value
    )
    assert (
        registry.initial_status(_supplier(ico=None, dic="DE123456789"))
        == SupplierRegistryStatus.PENDING.value
    )
    assert (
        registry.initial_status(_supplier(ico=None, dic=None))
        == SupplierRegistryStatus.NOT_CHECKABLE.value
    )


# --- VIES SOAP parsing --------------------------------------------------------


def _vies_body(valid: str, *, error: str | None = None) -> str:
    user_error = f"<ns2:userError>{error}</ns2:userError>" if error else ""
    name = "ACME s.r.o." if valid == "true" else "---"
    return (
        '<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">'
        "<env:Body><ns2:checkVatResponse "
        'xmlns:ns2="urn:ec.europa.eu:taxud:vies:services:checkVat:types">'
        f"<ns2:countryCode>CZ</ns2:countryCode><ns2:vatNumber>{VALID_ICO}</ns2:vatNumber>"
        f"<ns2:valid>{valid}</ns2:valid>{user_error}"
        f"<ns2:name>{name}</ns2:name><ns2:address>---/Praha</ns2:address>"
        "<ns2:requestIdentifier>CZ-M18.2-ABCDEF</ns2:requestIdentifier>"
        "</ns2:checkVatResponse></env:Body></env:Envelope>"
    )


def test_vies_parses_valid_response():
    provider = registry.ViesProvider("CZ" + VALID_ICO)
    record = provider._parse(_vies_body("true"))
    assert record is not None
    assert record.dic == "CZ" + VALID_ICO
    assert record.name == "ACME s.r.o."
    assert record.raw["vies_request_identifier"] == "CZ-M18.2-ABCDEF"


def test_vies_invalid_is_none():
    assert registry.ViesProvider("CZ" + VALID_ICO)._parse(_vies_body("false")) is None


def test_vies_concurrency_fault_is_unavailable():
    body = _vies_body("false", error="MS_MAX_CONCURRENT_REQ")
    with pytest.raises(registry.RegistryUnavailableError):
        registry.ViesProvider("CZ" + VALID_ICO)._parse(body)


# --- ADIS parsing -------------------------------------------------------------


def test_adis_parser_extracts_flags_and_accounts():
    from app.services import adis

    body = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soap:Body><ns:overeniPlatcuResponse xmlns:ns="http://adis.mfcr.cz/rdph">'
        "<ns:platce><ns:dic>CZ" + VALID_ICO + "</ns:dic>"
        "<ns:nespolehlivyPlatce>2026-03-01</ns:nespolehlivyPlatce>"
        "<ns:zverejnenyUcet>123456789/0100</ns:zverejnenyUcet>"
        "<ns:zverejnenyUcet>CZ65 0100 0000000001234567 89</ns:zverejnenyUcet>"
        "</ns:platce></ns:overeniPlatcuResponse></soap:Body></soap:Envelope>"
    )
    result = adis.parse_response(body)
    entry = result["CZ" + VALID_ICO]
    assert entry["unreliable"] is True
    assert entry["published_since"].isoformat() == "2026-03-01"
    assert "123456789/0100" in entry["published_accounts"]
    assert len(entry["published_accounts"]) == 2


# --- export gate integration --------------------------------------------------


def _make_ready_invoice(session: Session, ico: str) -> Invoice:
    company = create_company(session)
    invoice = Invoice(
        original_filename="f.pdf",
        storage_key=f"test/{uuid4().hex}.pdf",
        file_hash=uuid4().hex,
        content_type="application/pdf",
        file_size=1,
        state=InvoiceState.READY_FOR_EXPORT.value,
        recipient_company_id=company.id,
        supplier_ico=ico,
    )
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice


def test_claim_export_batch_holds_and_releases(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "REGISTRY_ENABLED", True)
    with Session(invoice_service.engine) as session:
        invoice = _make_ready_invoice(session, VALID_ICO)
        supplier = _supplier(registry_status=SupplierRegistryStatus.PENDING.value)
        session.add(supplier)
        session.commit()

        assert (
            invoice_service.claim_export_batch(
                session, invoice.recipient_company_id, limit=5
            )
            == []
        )

        supplier.registry_status = SupplierRegistryStatus.VERIFIED.value
        session.add(supplier)
        session.commit()
        claimed = invoice_service.claim_export_batch(
            session, invoice.recipient_company_id, limit=5
        )
        assert [row.id for row in claimed] == [invoice.id]

        # a fresh insolvency claim puts the next batch back behind the gate
        supplier.in_insolvency = True
        invoice.state = InvoiceState.READY_FOR_EXPORT.value
        session.add_all([supplier, invoice])
        session.commit()
        assert (
            invoice_service.claim_export_batch(
                session, invoice.recipient_company_id, limit=5
            )
            == []
        )

        supplier.export_confirmed_manually = True
        session.add(supplier)
        session.commit()
        claimed = invoice_service.claim_export_batch(
            session, invoice.recipient_company_id, limit=5
        )
        assert [row.id for row in claimed] == [invoice.id]


def test_periodic_job_sweeps_due_suppliers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "REGISTRY_ENABLED", True)
    refreshed: list[str] = []

    def fake_refresh(supplier: Supplier) -> bool:
        refreshed.append(str(supplier.id))
        supplier.registry_status = SupplierRegistryStatus.VERIFIED.value
        supplier.registry_next_check_at = get_datetime_utc() + timedelta(days=7)
        return True

    monkeypatch.setattr(registry, "refresh_supplier", fake_refresh)
    from app import tasks

    with Session(invoice_service.engine) as session:
        due = _supplier(
            name="Due s.r.o.",
            ico=make_ico(),
            registry_status=SupplierRegistryStatus.PENDING.value,
            registry_next_check_at=get_datetime_utc() - timedelta(minutes=1),
        )
        future = _supplier(
            name="Future s.r.o.",
            ico="00000099",
            registry_status=SupplierRegistryStatus.PENDING.value,
            registry_next_check_at=get_datetime_utc() + timedelta(hours=1),
        )
        session.add_all([due, future])
        session.commit()
        ids = {str(due.id)}

    tasks.registry_checks_job(timestamp=0)
    assert ids == set(refreshed)
