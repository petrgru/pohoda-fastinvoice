"""Registry queue jobs: per-supplier check, periodic sweep, nightly ADIS pass.

The suite runs with REGISTRY_ENABLED=False (conftest); the `enabled` fixture
opts a test into the registry paths exactly like production would.
"""

from datetime import timedelta
from typing import Any

import pytest
from sqlmodel import Session

from app import tasks
from app.core.config import settings
from app.core.db import engine
from app.models import Supplier, SupplierRegistryStatus, get_datetime_utc
from app.services import adis, registry
from tests.services.test_registry import _supplier, make_ico


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REGISTRY_ENABLED", True)


def test_check_task_verifies_and_persists(
    enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_refresh(supplier: Supplier) -> bool:
        supplier.registry_status = SupplierRegistryStatus.VERIFIED.value
        supplier.registry_next_check_at = get_datetime_utc() + timedelta(days=7)
        return True

    monkeypatch.setattr(registry, "refresh_supplier", fake_refresh)
    with Session(engine) as session:
        supplier = _supplier(
            ico=make_ico(), registry_status=SupplierRegistryStatus.PENDING.value
        )
        session.add(supplier)
        session.commit()
        supplier_id = str(supplier.id)

    tasks.check_supplier_registry_task(supplier_id)

    with Session(engine) as session:
        stored = session.get(Supplier, supplier_id)
        assert stored is not None
        assert stored.registry_status == SupplierRegistryStatus.VERIFIED.value


def test_check_task_missing_supplier_is_a_noop(enabled: None) -> None:
    tasks.check_supplier_registry_task("00000000-0000-0000-0000-000000000000")


def test_check_task_unavailable_keeps_the_gate(
    enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(_supplier: Supplier) -> bool:
        raise registry.RegistryUnavailableError("ARES is down")

    monkeypatch.setattr(registry, "refresh_supplier", explode)
    with Session(engine) as session:
        supplier = _supplier(
            ico=make_ico(), registry_status=SupplierRegistryStatus.PENDING.value
        )
        session.add(supplier)
        session.commit()
        supplier_id = str(supplier.id)

    tasks.check_supplier_registry_task(supplier_id)

    with Session(engine) as session:
        stored = session.get(Supplier, supplier_id)
        assert stored is not None
        # still gated, but a retry is scheduled per the backoff ladder
        assert stored.registry_status == SupplierRegistryStatus.PENDING.value
        assert stored.registry_check_attempts == 1
        assert stored.registry_next_check_at is not None
        assert stored.registry_next_check_at > get_datetime_utc()


def test_check_task_swallows_data_errors(
    enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_supplier: Supplier) -> bool:
        raise ValueError("malformed registry answer")

    monkeypatch.setattr(registry, "refresh_supplier", broken)
    with Session(engine) as session:
        supplier = _supplier(
            ico=make_ico(), registry_status=SupplierRegistryStatus.PENDING.value
        )
        session.add(supplier)
        session.commit()
        supplier_id = str(supplier.id)

    tasks.check_supplier_registry_task(supplier_id)

    with Session(engine) as session:
        stored = session.get(Supplier, supplier_id)
        assert stored is not None
        assert stored.registry_status == SupplierRegistryStatus.PENDING.value


def test_registry_checks_job_disabled_touches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "REGISTRY_ENABLED", False)
    called: list[Any] = []
    monkeypatch.setattr(registry, "refresh_supplier", lambda s: called.append(s))
    tasks.registry_checks_job(timestamp=0)
    assert called == []


def test_registry_checks_job_retries_on_outage(
    enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(_supplier: Supplier) -> bool:
        raise registry.RegistryUnavailableError("VIES is down")

    monkeypatch.setattr(registry, "refresh_supplier", explode)
    with Session(engine) as session:
        supplier = _supplier(
            ico=make_ico(),
            registry_status=SupplierRegistryStatus.PENDING.value,
            registry_next_check_at=get_datetime_utc() - timedelta(minutes=1),
        )
        session.add(supplier)
        session.commit()
        supplier_id = str(supplier.id)

    tasks.registry_checks_job(timestamp=0)

    with Session(engine) as session:
        stored = session.get(Supplier, supplier_id)
        assert stored is not None
        # the outage must not flip the status, only push the next check out
        assert stored.registry_status == SupplierRegistryStatus.PENDING.value
        assert stored.registry_next_check_at is not None
        assert stored.registry_next_check_at > get_datetime_utc()


def _verified_payable(session: Session) -> Supplier:
    supplier = _supplier(
        ico=make_ico(),
        dic="CZ" + make_ico(),
        registry_status=SupplierRegistryStatus.VERIFIED.value,
    )
    session.add(supplier)
    session.commit()
    return supplier


def test_adis_job_sets_flags_and_stores_accounts(
    enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(engine) as session:
        supplier = _verified_payable(session)
        supplier_id = supplier.id
        dic = supplier.dic

    monkeypatch.setattr(
        adis,
        "check_batch",
        lambda dics: {
            dic: {
                "unreliable": True,
                "published_accounts": ["2401384534/2010"],
            }
        },
    )
    tasks.adis_reliability_job(timestamp=0)

    with Session(engine) as session:
        stored = session.get(Supplier, supplier_id)
        assert stored is not None
        assert stored.unreliable_payer is True
        data: dict[str, Any] = stored.registry_data or {}
        assert data["published_accounts"] == ["2401384534/2010"]


def test_adis_job_defers_on_outage(
    enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(engine) as session:
        supplier = _verified_payable(session)
        supplier_id = supplier.id

    def explode(_dics: list[str]) -> dict[str, dict[str, Any]]:
        raise adis.AdisUnavailableError("ADIS is down")

    monkeypatch.setattr(adis, "check_batch", explode)
    tasks.adis_reliability_job(timestamp=0)

    with Session(engine) as session:
        stored = session.get(Supplier, supplier_id)
        assert stored is not None
        assert stored.unreliable_payer is False


def test_adis_job_disabled_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REGISTRY_ENABLED", False)
    called: list[Any] = []
    monkeypatch.setattr(adis, "check_batch", lambda dics: called.append(dics))
    tasks.adis_reliability_job(timestamp=0)
    assert called == []
