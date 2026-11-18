"""Supplier verification against the company registries (ARES / ADIS / VIES).

ARES REST (Ministry of Finance, JSON, no API key) is the primary source for
Czech subjects: existence, registered name/office, VAT-payer flag and the
insolvency-register flag. ADIS (MOJE daně SOAP, bulk up to 100 DIČ) adds the
unreliable-payer status and the officially published bank accounts. VIES
(official EC SOAP) validates cross-border EU VAT numbers.

Everything calls from the worker queue - the API never blocks on a registry.
A registry that is unreachable, rate-limited or degraded is *not* a failure:
the supplier stays PENDING and the export gate holds the invoice until a
confirmation arrives (backoff schedule in :func:`next_backoff`).

Parsing is deliberately tolerant: the exact JSON/XML field paths are looked up
by key-name patterns rather than a hardcoded tree, so a registry schema
revision does not silently turn every answer into "not found".
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from xml.etree import ElementTree

import httpx

from app.core.config import settings
from app.models import (
    Supplier,
    SupplierRegistryStatus,
    get_datetime_utc,
)

logger = logging.getLogger(__name__)

#: exponential retry ladder when a registry is unreachable (capped at 24 h)
_BACKOFF_STEPS = [timedelta(minutes=10), timedelta(hours=1), timedelta(hours=6)]
#: a "not found" answer is re-tried daily - cheap insurance against registry
#: outages that answer 404 while being broken
_NOT_FOUND_RETRY = timedelta(hours=24)

_last_call_at = 0.0


class RegistryUnavailableError(RuntimeError):
    """Transport/5xx/429/SOAP fault - outcome unknown, retry later."""


@dataclass
class RegistryRecord:
    """Parsed answer of one registry lookup (see :class:`AresProvider`)."""

    name: str | None = None
    address: str | None = None
    dic: str | None = None
    vat_payer: bool | None = None
    in_insolvency: bool = False
    insolvency_info: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def ico_checksum_ok(ico: str | None) -> bool:
    """Czech IČO: 8 digits whose weighted sum (8..1) is divisible by 11.

    Filtered before the query so a typo never hits the registry - repeated
    bogus lookups are what ARES blocks an IP for.
    """
    if not ico or not re.fullmatch(r"\d{8}", ico):
        return False
    weights = (8, 7, 6, 5, 4, 3, 2, 1)
    total = sum(int(digit) * weight for digit, weight in zip(ico, weights, strict=True))
    return total % 11 == 0


def next_backoff(attempts: int) -> timedelta:
    """Retry delay after `attempts` failed/unknown outcomes (10 m, 1 h, 6 h, 24 h)."""
    if not attempts:
        return timedelta(0)
    if attempts > len(_BACKOFF_STEPS):
        return timedelta(hours=24)
    return _BACKOFF_STEPS[attempts - 1]


def export_block_reason(supplier: Supplier | None) -> str | None:
    """Why the connector must not claim an invoice of this supplier (UI chip)."""
    if supplier is None:
        return None
    status = supplier.registry_status
    if status == SupplierRegistryStatus.PENDING.value:
        return "registry_pending"
    if status == SupplierRegistryStatus.NOT_FOUND.value:
        return "registry_not_found"
    if supplier.in_insolvency and not supplier.export_confirmed_manually:
        return "supplier_insolvency"
    return None


def export_allowed(supplier: Supplier | None) -> bool:
    return export_block_reason(supplier) is None


# --- providers ---------------------------------------------------------------


def _polite_pause() -> None:
    """Keep a minimum gap between two registry calls (worker is sequential)."""
    global _last_call_at
    wait = settings.REGISTRY_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.monotonic()


def _walk_dicts(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict found anywhere inside a parsed JSON document."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _pick_str(node: dict[str, Any], *keys: str) -> str | None:
    """String (or compactly flattened object) under the first matching key."""
    lowered = {k.lower(): v for k, v in node.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            parts = [
                str(v).strip()
                for v in value.values()
                if isinstance(v, str | int) and str(v).strip()
            ]
            if parts:
                return ", ".join(parts)
    return None


def _find_flag(node: dict[str, Any], *patterns: re.Pattern[str]) -> str | None:
    """First key matching any pattern; returns its raw string value."""
    for key, value in node.items():
        if any(p.search(key.lower()) for p in patterns):
            if value in (None, "", 0, False) or str(value).lower() in (
                "0",
                "ne",
                "no",
                "false",
                "n",
            ):
                continue
            return str(value)
    return None


def ares_request_url(ico: str) -> str:
    """Full ARES REST URL for one IČO (the base may carry query params, e.g. json=1)."""
    template = settings.ARES_REST_URL
    if "{" in template:
        return template.format(ico=ico)
    separator = "&" if "?" in template else "?"
    return f"{template}{separator}ico={ico}"


class AresProvider:
    """New ARES REST API (Czech Ministry of Finance, JSON, keyless).

    Official limits: 500 queries/min/IP; blocks abusive or parallel scanning,
    so we query single-threaded through one worker with a pause between calls.
    """

    _NAME_KEYS = ("obchodni_jmeno", "obchodnijmeno", "firma", "nazev", "name")
    _ADDRESS_KEYS = ("sidlo", "adresnimisto", "adresa", "address")
    _DIC_KEYS = ("dic", "ident_cis_dfs", "id_dph", "vat")
    # insolvency register presence: ARES marks it on the subject record
    # (úpadek date, or a boolean/text flag on the IRS register membership)
    _INSOLVENCY_KEY = re.compile(r"upadk|insolv|\birs?\b|registr_ir", re.IGNORECASE)
    # VAT payer registration flag(s) (REG_DP / plátce DPH family of names)
    _VAT_KEY = re.compile(
        r"platce_dph|platcedph|reg_dp|ident_cis_dp|vatpayer", re.IGNORECASE
    )

    def lookup_ico(self, ico: str) -> RegistryRecord | None:
        """Return the parsed record, or None when ARES answers 404 (unknown)."""
        _polite_pause()
        url = ares_request_url(ico)
        try:
            response = httpx.get(
                url,
                timeout=settings.REGISTRY_TIMEOUT_SECONDS,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise RegistryUnavailableError(str(exc)) from exc
        if response.status_code == 404:
            return None
        if response.status_code == 429 or response.status_code >= 500:
            raise RegistryUnavailableError(f"ARES HTTP {response.status_code}")
        try:
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RegistryUnavailableError(f"ARES response: {exc}") from exc
        return self._parse(data)

    def _parse(self, data: Any) -> RegistryRecord | None:
        record = RegistryRecord(raw=data if isinstance(data, dict) else {})
        found = False
        for node in _walk_dicts(data):
            name = _pick_str(node, *self._NAME_KEYS)
            if name and not record.name:
                record.name = name
                found = True
            address = _pick_str(node, *self._ADDRESS_KEYS)
            if address and not record.address:
                record.address = address
            dic = _pick_str(node, *self._DIC_KEYS)
            if dic and not record.dic:
                record.dic = dic
            insolvency = _find_flag(node, self._INSOLVENCY_KEY)
            if insolvency and not record.in_insolvency:
                record.in_insolvency = True
                record.insolvency_info = {"source": "ares", "detail": insolvency}
            vat_flag = _find_flag(node, self._VAT_KEY)
            if vat_flag is not None and record.vat_payer is None:
                record.vat_payer = vat_flag.lower() not in ("false", "0", "ne")
        if not found:
            return None
        return record


class ViesProvider:
    """Official VIES SOAP ``checkVat`` - EU cross-border VAT validation."""

    _NS = "urn:ec.europa.eu:taxud:vies:services:checkVat:types"
    _ENVELOPE = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
        f' xmlns:tns="{_NS}">'
        "<soap:Body><tns:checkVat>"
        "<tns:countryCode>{country}</tns:countryCode>"
        "<tns:vatNumber>{number}</tns:vatNumber>"
        "</tns:checkVat></soap:Body></soap:Envelope>"
    )

    def __init__(self, dic: str) -> None:
        normalized = dic.replace(" ", "").upper()
        match = re.match(r"^([A-Z]{2})(\d{8,12})$", normalized)
        if not match:
            raise ValueError(f"not an EU VAT number: {dic}")
        self.country, self.number = match.groups()
        self.dic = normalized

    def check(self) -> RegistryRecord | None:
        """Parsed record when valid; None when VIES says INVALID/unknown."""
        _polite_pause()
        try:
            response = httpx.post(
                settings.VIES_SOAP_URL,
                content=self._ENVELOPE.format(country=self.country, number=self.number),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": "checkVat",
                },
                timeout=settings.REGISTRY_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise RegistryUnavailableError(str(exc)) from exc
        if response.status_code >= 500:
            raise RegistryUnavailableError(f"VIES HTTP {response.status_code}")
        return self._parse(response.text)

    def _parse(self, body: str) -> RegistryRecord | None:
        try:
            root = ElementTree.fromstring(body)
        # not XML at all -> member state error page
        except ElementTree.ParseError as exc:
            raise RegistryUnavailableError(f"VIES response: {exc}") from exc

        def text(tag: str) -> str | None:
            element = root.find(f".//{{{self._NS}}}{tag}")
            if element is None:
                return None
            return (element.text or "").strip() or None

        # the Czech node reports MS_MAX_CONCURRENT_REQ instead of an answer
        if (error := text("userError")) and "CONCURRENT" in error.upper():
            raise RegistryUnavailableError(f"VIES {error}")
        valid = (text("valid") or "").lower()
        if valid != "true":
            return None
        name = text("name")
        address = text("address")
        return RegistryRecord(
            name=None if name in ("---", None) else name,
            address=None if address in ("---", None) else address,
            dic=f"{self.country}{self.number}",
            vat_payer=True,
            raw={"vies_request_identifier": text("requestIdentifier")},
        )


# --- supplier state transitions ----------------------------------------------


def schedule_retry(supplier: Supplier) -> None:
    """Unknown outcome: keep PENDING, retry after the backoff ladder."""
    supplier.registry_check_attempts += 1
    delay = next_backoff(supplier.registry_check_attempts)
    horizon = get_datetime_utc()
    supplier.registry_next_check_at = horizon + delay
    supplier.updated_at = horizon


def apply_record(supplier: Supplier, record: RegistryRecord, source: str) -> None:
    """Registry confirmed the subject (or explicitly did not know it)."""
    now = get_datetime_utc()
    supplier.registry_status = SupplierRegistryStatus.VERIFIED.value
    supplier.registry_check_attempts = 0
    supplier.registry_checked_at = now
    supplier.registry_next_check_at = now + timedelta(
        days=settings.INSOLVENCY_RECHECK_DAYS
    )
    supplier.in_insolvency = record.in_insolvency
    supplier.insolvency_info = record.insolvency_info
    data = {
        "source": source,
        "name": record.name,
        "address": record.address,
        "dic": record.dic,
        "vat_payer": record.vat_payer,
    }
    supplier.registry_data = {k: v for k, v in data.items() if v is not None}
    supplier.updated_at = now


def mark_not_found(supplier: Supplier) -> None:
    now = get_datetime_utc()
    supplier.registry_status = SupplierRegistryStatus.NOT_FOUND.value
    supplier.registry_check_attempts += 1
    supplier.registry_checked_at = now
    supplier.registry_next_check_at = now + _NOT_FOUND_RETRY
    supplier.updated_at = now


def refresh_supplier(supplier: Supplier) -> bool:
    """Run the verification chain for one supplier; True when it ran.

    CZ path: ARES by IČO (mod-11 checked first, so typos never leave here).
    EU path: VIES by the prefixed DIČ. Anything else is NOT_CHECKABLE and
    never blocks anything.
    """
    if not settings.REGISTRY_ENABLED:
        return False
    ico = supplier.ico
    dic = (supplier.dic or "").replace(" ", "").upper()
    if ico:
        if not ico_checksum_ok(ico):
            supplier.registry_status = SupplierRegistryStatus.ICO_INVALID.value
            supplier.registry_next_check_at = None
            supplier.updated_at = get_datetime_utc()
            return True
        record = AresProvider().lookup_ico(ico)
        if record is None:
            mark_not_found(supplier)
        else:
            apply_record(supplier, record, source="ares")
        return True
    if re.match(r"^[A-Z]{2}\d{8,12}$", dic):
        # raises RegistryUnavailableError on outages - the caller schedules
        # the retry, keeping one backoff path for every provider
        record = ViesProvider(dic).check()
        if record is None:
            mark_not_found(supplier)
        else:
            apply_record(supplier, record, source="vies")
        return True
    supplier.registry_status = SupplierRegistryStatus.NOT_CHECKABLE.value
    supplier.registry_next_check_at = None
    supplier.updated_at = get_datetime_utc()
    return True


def initial_status(supplier: Supplier) -> str:
    """Status a freshly registered supplier starts with."""
    if not settings.REGISTRY_ENABLED:
        # verification off -> nothing may be held behind a gate that never runs
        return SupplierRegistryStatus.NOT_CHECKABLE.value
    ico = supplier.ico
    if ico and not ico_checksum_ok(ico):
        return SupplierRegistryStatus.ICO_INVALID.value
    if ico:
        return SupplierRegistryStatus.PENDING.value
    if re.match(r"^[A-Z]{2}\d{8,12}$", (supplier.dic or "").replace(" ", "").upper()):
        return SupplierRegistryStatus.PENDING.value
    return SupplierRegistryStatus.NOT_CHECKABLE.value
