"""VAT (DIČ) validation against the official VIES service.

Replaces the old mod-11 checksum rule and the VatComply proxy that reported
Czech payers as invalid (VAT-comply.cz was a rate-limited middleman over the
same VIES data with its own outages). A bare domestic DIČ (no country prefix)
identifies a non-payer - VIES cannot validate those, so they are accepted and
left to the registry verification (app.services.registry) for existence.

Results are cached in memory for a while; an unreachable VIES means
"unavailable" (fail-open) so saving a company never depends on an EU service.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.config import settings
from app.services import registry

# country code + 8-12 alphanumerics (CZ: CZ + 8 digits) or bare 8-10 digits
_VAT_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{8,12}$|^\d{8,10}$")

# dic -> (expiry timestamp, result); tiny cache against the rate limit
_cache: dict[str, tuple[float, VatResult]] = {}
_CACHE_TTL_SECONDS = 86_400  # VAT registrations change rarely
_CACHE_MAX_ENTRIES = 256
# one fetch per DIČ at a time - concurrent requests (autofill + save) share it
_fetch_locks: dict[str, threading.Lock] = {}
_fetch_locks_guard = threading.Lock()


class VatResult(BaseModel):
    valid: bool
    name: str | None = None
    address: str | None = None
    country_code: str | None = None
    request_identifier: str | None = None
    # True when the external service could not be reached (fail-open)
    unavailable: bool = False


class VatServiceUnavailableError(RuntimeError):
    """Raised when VIES cannot be consulted."""


class VatInvalidError(ValueError):
    """Raised when VIES explicitly reports the VAT number as invalid."""


def normalize_dic(value: str) -> str:
    return value.replace(" ", "").upper()


def is_valid_format(dic: str) -> bool:
    return bool(_VAT_RE.match(normalize_dic(dic)))


def _fetch(dic: str) -> Any:
    """One VIES checkVat round-trip via the shared provider, as a plain dict."""
    record = registry.ViesProvider(dic).check()
    if record is None:
        return {"valid": False}
    return {
        "valid": True,
        "name": record.name,
        "address": record.address,
        "country_code": dic[:2],
        "request_identifier": record.raw.get("vies_request_identifier"),
    }


def lookup_vat(dic: str) -> VatResult:
    """Validate `dic` and return the registered company data when available."""
    dic = normalize_dic(dic)
    if not is_valid_format(dic):
        return VatResult(valid=False)
    if not settings.REGISTRY_ENABLED:
        return VatResult(valid=True, unavailable=True)
    if not re.match(r"^[A-Z]{2}\d{8,12}$", dic):
        # domestic-only DIČ (non-payer): outside VIES scope, nothing to check
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
            data: dict[str, Any] = _fetch(dic)
        except registry.RegistryUnavailableError, httpx.HTTPError:
            # transport faults and member-state errors -> fail-open
            return VatResult(valid=True, unavailable=True)
        except ValueError:
            return VatResult(valid=False)
        else:
            result = VatResult(
                valid=bool(data.get("valid", False)),
                name=(data.get("name") or "").strip() or None,
                address=(data.get("address") or "").strip() or None,
                country_code=data.get("country_code"),
                request_identifier=data.get("request_identifier"),
            )
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            _cache.clear()
        _cache[dic] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
        return result


def check_dic_or_raise(dic: str) -> VatResult | None:
    """Blocking check used when saving a company.

    * format-invalid or VIES-invalid -> VatInvalidError (save is blocked)
    * service unreachable -> log-and-continue (fail-open), result or None
    * domestic-only DIČ -> accepted (see module docstring)
    """
    result = lookup_vat(dic)
    if result.unavailable:
        return result
    if not result.valid:
        raise VatInvalidError(normalize_dic(dic))
    return result
