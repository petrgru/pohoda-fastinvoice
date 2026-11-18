"""ADIS (MOJE daně) - VAT register bulk checks for the supplier registry.

Runs nightly from the queue: one SOAP request carries up to 100 DIČ and
returns the unreliable-payer status plus the officially published bank
accounts of each registered payer. The answers enrich the supplier registry
only (warning badges, account cross-check) - the export gate never depends
on ADIS, so an outage or a schema surprise here is a logged no-op until the
next pass.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any
from xml.etree import ElementTree

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: ADIS tolerates at most 100 DIČ per request
MAX_BATCH = 100

# response elements are matched case-insensitively on purpose: the exact
# element casing differs between ADIS environments (calibrate via
# scripts/check_registries.py when wiring a new deployment)
_UNSOLVABLE_RE = re.compile(r"nespolehliv|unreliable", re.IGNORECASE)
_ACCOUNT_RE = re.compile(r"ucet|ucty|account|iban", re.IGNORECASE)


class AdisUnavailableError(RuntimeError):
    """Service unreachable / unexpected schema - retry on the next pass."""


def _endpoint() -> str:
    return settings.ADIS_WSDL_URL.replace("?wsdl", "").rstrip("/")


def _envelope(dics: list[str]) -> str:
    body = "".join(f"<ns:dic>{dic}</ns:dic>" for dic in dics)
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:ns="http://adis.mfcr.cz/rdph">'
        "<soap:Body>"
        f"<ns:overeniPlatcu>{body}</ns:overeniPlatcu>"
        "</soap:Body></soap:Envelope>"
    )


def _iter_elements(root: ElementTree.Element) -> Any:
    return root.iter()


def parse_response(body: str) -> dict[str, dict[str, Any]]:
    """dic -> {unreliable: bool, published_accounts: [..], published_since}."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise AdisUnavailableError(f"ADIS response: {exc}") from exc
    results: dict[str, dict[str, Any]] = {}
    for item in _iter_elements(root):
        tag = item.tag.rsplit("}", 1)[-1].lower()
        if "platce" not in tag and "vyslede" not in tag and "item" not in tag:
            continue
        dic = None
        entry: dict[str, Any] = {"unreliable": False, "published_accounts": []}
        for child in item.iter():
            ctag = child.tag.rsplit("}", 1)[-1]
            text = (child.text or "").strip()
            if not text and not len(child):
                continue
            if ctag.lower() in ("dic", "iddph"):
                dic = text or dic
            elif _UNSOLVABLE_RE.search(ctag) and text.lower() not in (
                "",
                "0",
                "false",
                "ne",
            ):
                entry["unreliable"] = True
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                    entry["published_since"] = date.fromisoformat(text)
            elif _ACCOUNT_RE.search(ctag) and re.search(r"\d{6,}|\bGB", text):
                entry["published_accounts"].append(text.replace(" ", "").upper())
        if dic:
            results[dic.upper()] = entry
    return results


def check_batch(dics: list[str]) -> dict[str, dict[str, Any]]:
    """One bulk ADIS request; {} when nothing matched or service is quiet."""
    dics = [d for d in dics if re.match(r"^CZ\d{8,10}$", d)]
    if not dics:
        return {}
    try:
        response = httpx.post(
            _endpoint(),
            content=_envelope(dics[:MAX_BATCH]),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=settings.REGISTRY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise AdisUnavailableError(str(exc)) from exc
    if response.status_code >= 500:
        raise AdisUnavailableError(f"ADIS HTTP {response.status_code}")
    return parse_response(response.text)
