"""VAT (DIČ) validation and company lookup via the VatComply API.

Replaces the old mod-11 checksum rule – EU VAT numbers are checked against
the official VIES database. The service needs no API key but is rate limited
(2 req/s), so results are cached in memory for a while.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.config import settings

# country code + 8-12 alphanumerics (CZ: CZ + 8 digits) or bare 8-10 digits
_VAT_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{8,12}$|^\d{8,10}$")

# dic -> (expiry timestamp, result); tiny LRU-ish cache against the rate limit
_cache: dict[str, tuple[float, VatResult]] = {}
_CACHE_TTL_SECONDS = 86_400  # VAT registrations change rarely
_CACHE_MAX_ENTRIES = 256
# one fetch per DIČ at a time – concurrent requests (autofill + save) share it
_fetch_locks: dict[str, threading.Lock] = {}
_fetch_locks_guard = threading.Lock()


class VatResult(BaseModel):
    valid: bool
    name: str | None = None
    address: str | None = None
    country_code: str | None = None
    # True when the external service could not be reached (fail-open)
    unavailable: bool = False


class VatServiceUnavailableError(RuntimeError):
    """Raised when VatComply/VIES cannot be consulted."""


class VatInvalidError(ValueError):
    """Raised when VIES explicitly reports the VAT number as invalid."""


def normalize_dic(value: str) -> str:
    return value.replace(" ", "").upper()


def is_valid_format(dic: str) -> bool:
    return bool(_VAT_RE.match(normalize_dic(dic)))


def _fetch(dic: str) -> dict[str, Any]:
    """One VatComply request; raises httpx.HTTPError on failure."""
    url = f"{settings.VATCOMPLY_BASE_URL.rstrip('/')}/vat"
    response = httpx.get(
        url,
        params={"vat_number": dic},
        timeout=settings.VATCOMPLY_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return dict(response.json())


def lookup_vat(dic: str) -> VatResult:
    """Validate `dic` and return the registered company data when available."""
    dic = normalize_dic(dic)
    if not is_valid_format(dic):
        return VatResult(valid=False)
    if not settings.VATCOMPLY_ENABLED:
        return VatResult(valid=True, unavailable=True)
    cached = _cache.get(dic)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    with _fetch_locks_guard:
        lock = _fetch_locks.setdefault(dic, threading.Lock())
    with lock:
        # another request may have filled the cache while we waited
        cached = _cache.get(dic)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            data = _fetch(dic)
        except httpx.HTTPStatusError as exc:
            # 503 / transient VIES faults -> fail-open; other 4xx -> as unknown
            if exc.response.status_code in (400, 404):
                result = VatResult(valid=False)
            else:
                return VatResult(valid=True, unavailable=True)
        except httpx.HTTPError, ValueError:
            return VatResult(valid=True, unavailable=True)
        else:
            result = VatResult(
                valid=bool(data.get("valid", False)),
                name=(data.get("name") or "").strip() or None,
                address=(data.get("address") or "").strip() or None,
                country_code=data.get("country_code"),
            )
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            _cache.clear()
        _cache[dic] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
        return result


def check_dic_or_raise(dic: str) -> VatResult | None:
    """Blocking check used when saving a company.

    * format-invalid or VIES-invalid -> VatInvalidError (save is blocked)
    * service unreachable -> log-and-continue (fail-open), result or None
    """
    result = lookup_vat(dic)
    if result.unavailable:
        return result
    if not result.valid:
        raise VatInvalidError(normalize_dic(dic))
    return result
