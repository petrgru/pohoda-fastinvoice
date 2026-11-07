"""Optional Meilisearch mirror of the invoice full-text index.

Postgres remains the source of truth: every Meilisearch call is best-effort
and a failure only logs a warning – search then falls back to the database.
"""

import logging
import uuid
from typing import Any

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.models import Invoice, RecipientCompany

logger = logging.getLogger(__name__)

INDEX = "invoices"
_OCR_LIMIT = 30_000
_index_ready = False


def _request(method: str, path: str, **kw: Any) -> httpx.Response | None:
    """One Meilisearch call; None when disabled or unreachable."""
    if not settings.MEILISEARCH_ENABLED:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.MEILISEARCH_MASTER_KEY:
        headers["Authorization"] = f"Bearer {settings.MEILISEARCH_MASTER_KEY}"
    try:
        response = httpx.request(
            method,
            f"{settings.MEILISEARCH_URL.rstrip('/')}{path}",
            headers=headers,
            timeout=3.0,
            **kw,
        )
        response.raise_for_status()
        return response
    except httpx.HTTPError as exc:  # noqa: PERF203 - best-effort by design
        logger.warning("meilisearch %s %s failed: %s", method, path, exc)
        return None


def _ensure_index() -> None:
    global _index_ready  # noqa: PLW0603 - lazy one-time setup
    if _index_ready or not settings.MEILISEARCH_ENABLED:
        return
    if _request("GET", f"/indexes/{INDEX}") is None:
        _request("POST", "/indexes", json={"uid": INDEX, "primaryKey": "id"})
    _request(
        "PATCH",
        f"/indexes/{INDEX}/settings",
        json={
            "searchableAttributes": [
                "invoice_number",
                "supplier_name",
                "company_name",
                "supplier_ico",
                "supplier_dic",
                "accounts",
                "original_filename",
                "ocr_text",
            ],
            "filterableAttributes": ["state", "recipient_company_id"],
            "sortableAttributes": ["created_at"],
            "displayedAttributes": ["id"],
        },
    )
    _index_ready = True


def _document(session: Session, invoice: Invoice) -> dict[str, Any]:
    company = (
        session.get(RecipientCompany, invoice.recipient_company_id)
        if invoice.recipient_company_id
        else None
    )
    accounts = [invoice.supplier_bank_account or "", invoice.supplier_iban or ""]
    accounts += [
        f"{row.get('account') or ''} {row.get('iban') or ''}"
        for row in (invoice.supplier_bank_accounts or [])
    ]
    return {
        "id": str(invoice.id),
        "invoice_number": invoice.invoice_number or "",
        "supplier_name": invoice.supplier_name or "",
        "company_name": company.name if company else "",
        "supplier_ico": invoice.supplier_ico or "",
        "supplier_dic": invoice.supplier_dic or "",
        "accounts": " ".join(accounts),
        "original_filename": invoice.original_filename or "",
        "ocr_text": (invoice.ocr_text or "")[:_OCR_LIMIT],
        "state": str(invoice.state),
        "recipient_company_id": str(invoice.recipient_company_id)
        if invoice.recipient_company_id
        else None,
    }


def sync(session: Session, invoice: Invoice) -> None:
    """Upsert one invoice into the index (fire and forget)."""
    if not settings.MEILISEARCH_ENABLED:
        return
    _ensure_index()
    _request("POST", f"/indexes/{INDEX}/documents", json=[_document(session, invoice)])


def remove(invoice_id: uuid.UUID) -> None:
    if not settings.MEILISEARCH_ENABLED:
        return
    _ensure_index()
    _request("DELETE", f"/indexes/{INDEX}/documents/{invoice_id}")


def search_ids(
    query: str,
    *,
    state: str | None = None,
    recipient_company_id: uuid.UUID | None = None,
    limit: int,
    offset: int,
) -> tuple[list[uuid.UUID], int] | None:
    """Ranked invoice ids for `query`, or None to fall back to Postgres."""
    _ensure_index()
    filters = []
    if state:
        filters.append(f"state = '{state}'")
    if recipient_company_id:
        filters.append(f"recipient_company_id = '{recipient_company_id}'")
    body: dict[str, Any] = {
        "q": query,
        "limit": limit,
        "offset": offset,
        "attributesToRetrieve": ["id"],
    }
    if filters:
        body["filter"] = " AND ".join(filters)
    response = _request("POST", f"/indexes/{INDEX}/search", json=body)
    if response is None:
        return None
    payload = response.json()
    try:
        ids = [uuid.UUID(hit["id"]) for hit in payload["hits"]]
    except KeyError, ValueError:
        return None
    return ids, int(payload.get("estimatedTotalHits", len(ids)))


def reindex_all(session: Session) -> int:
    """Push every invoice into a fresh index; returns the document count."""
    if not settings.MEILISEARCH_ENABLED:
        return 0
    _ensure_index()
    # drop first so documents of deleted invoices cannot linger as ghosts
    _request("DELETE", f"/indexes/{INDEX}")
    global _index_ready  # noqa: PLW0603 - the index object is gone
    _index_ready = False
    _ensure_index()
    invoices = session.exec(select(Invoice)).all()
    documents = [_document(session, invoice) for invoice in invoices]
    for chunk_start in range(0, len(documents), 500):
        _request(
            "POST",
            f"/indexes/{INDEX}/documents",
            json=documents[chunk_start : chunk_start + 500],
        )
    return len(documents)
