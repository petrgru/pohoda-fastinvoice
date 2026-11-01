import httpx
import pytest

from app.core.config import settings
from app.services import vat


@pytest.fixture(autouse=True)
def _vat_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "VATCOMPLY_ENABLED", True)
    vat._cache.clear()
    yield
    vat._cache.clear()


def test_format_checks():
    assert vat.is_valid_format("CZ06846904")
    assert vat.is_valid_format("cz 06846904")
    assert vat.is_valid_format("06846904")
    assert vat.is_valid_format("DE123456789")
    assert not vat.is_valid_format("CZ123")
    assert not vat.is_valid_format("!")


def test_invalid_dic_is_not_saved(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vat, "_fetch", lambda dic: {"valid": False, "vat_number": dic})
    with pytest.raises(vat.VatInvalidError):
        vat.check_dic_or_raise("CZ11111111")


def test_valid_dic_returns_registry_data(monkeypatch: pytest.MonkeyPatch):
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
    result = vat.check_dic_or_raise("CZ06846904")
    assert result is not None
    assert result.valid
    assert result.name == "BNET Business s.r.o."
    assert result.address == "Nákladní 3002/2, Opava"


def test_unreachable_service_fails_open(monkeypatch: pytest.MonkeyPatch):
    def boom(dic: str) -> dict:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(vat, "_fetch", boom)
    result = vat.check_dic_or_raise("CZ06846904")  # must not raise
    assert result is not None
    assert result.unavailable


def test_vies_transient_fault_fails_open(monkeypatch: pytest.MonkeyPatch):
    request = httpx.Request("GET", "https://api.vatcomply.com/vat")
    response = httpx.Response(503, request=request)

    def unavailable(dic: str) -> dict:
        raise httpx.HTTPStatusError(
            "MS_UNAVAILABLE", request=request, response=response
        )

    monkeypatch.setattr(vat, "_fetch", unavailable)
    result = vat.check_dic_or_raise("CZ06846904")  # must not raise
    assert result is not None
    assert result.unavailable


def test_lookup_is_cached(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def counting_fetch(dic: str) -> dict:
        calls.append(dic)
        return {"valid": True, "name": "X"}

    monkeypatch.setattr(vat, "_fetch", counting_fetch)
    vat.lookup_vat("CZ06846904")
    vat.lookup_vat("cz 06846904")  # normalized to the same cache key
    assert calls == ["CZ06846904"]


def test_disabled_service_skips_check(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "VATCOMPLY_ENABLED", False)
    monkeypatch.setattr(settings, "VATCOMPLY_BASE_URL", "http://unreachable.invalid")
    result = vat.check_dic_or_raise("CZ06846904")  # no HTTP, no error
    assert result is not None
    assert result.unavailable
