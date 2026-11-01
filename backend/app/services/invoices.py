"""Invoice workflow service: state machine, approval, queue, FTS, audit.

State transitions are only performed here (never directly from routes),
see `InvoiceState` in app.models for the diagram.
"""

import logging
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text as sa_text
from sqlmodel import Session

from app.core.db import engine
from app.models import (
    DELETABLE_STATES,
    AuditLog,
    Company,
    Invoice,
    InvoicePublic,
    InvoiceState,
    InvoiceStateCounts,
    InvoiceStats,
    InvoiceStatsBucket,
    ParsedInvoice,
    User,
    as_vat_items,
    check_vat_items,
    get_datetime_utc,
    vat_items_to_json,
    vat_rate_warnings,
)
from app.services import parsing, search_index, storage

logger = logging.getLogger(__name__)

# Transitions allowed by the workflow.
_ALLOWED_TRANSITIONS: dict[InvoiceState, set[InvoiceState]] = {
    InvoiceState.UPLOADED: {InvoiceState.PARSING},
    InvoiceState.PARSING: {InvoiceState.NEEDS_REVIEW},
    InvoiceState.NEEDS_REVIEW: {InvoiceState.READY_FOR_EXPORT, InvoiceState.PARSING},
    InvoiceState.READY_FOR_EXPORT: {
        InvoiceState.EXPORTED,
        InvoiceState.EXPORT_FAILED,
        InvoiceState.NEEDS_REVIEW,
    },
    InvoiceState.EXPORT_FAILED: {
        InvoiceState.NEEDS_REVIEW,
        InvoiceState.READY_FOR_EXPORT,
        InvoiceState.EXPORTED,
    },
    InvoiceState.EXPORTED: set(),
}


class InvoiceLockedError(PermissionError):
    """Raised when a record can no longer be modified or deleted."""


class InvalidTransitionError(ValueError):
    pass


class ApprovalNotAllowedError(ValueError):
    pass


def log_audit(
    session: Session,
    action: str,
    *,
    invoice: Invoice | None = None,
    company: Company | None = None,
    user: User | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            invoice_id=invoice.id if invoice else None,
            company_id=company.id if company else None,
            user_id=user.id if user else None,
            details=details,
        )
    )


def change_state(
    session: Session,
    invoice: Invoice,
    new_state: InvoiceState,
    *,
    note: str | None = None,
    user: User | None = None,
) -> Invoice:
    current = InvoiceState(invoice.state)
    if new_state not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"{current.value} -> {new_state.value}")
    invoice.state = new_state.value
    invoice.state_note = note
    invoice.updated_at = get_datetime_utc()
    session.add(invoice)
    log_audit(
        session,
        "state_change",
        invoice=invoice,
        user=user,
        details={"from": current.value, "to": new_state.value, "note": note},
    )
    return invoice


def recompute_derived(invoice: Invoice) -> None:
    """Fill totals from VAT items and run the DPH consistency check."""
    items = as_vat_items(invoice.vat_items) or []
    invoice.vat_items = vat_items_to_json(items or None)
    # totals are derived from the lines in the invoice currency only – a
    # foreign document may list informational amounts in a second currency
    relevant = [
        item
        for item in items
        if invoice.currency is None
        or item.currency is None
        or item.currency == invoice.currency
    ]
    if relevant:
        if invoice.total_without_vat is None:
            invoice.total_without_vat = sum(
                (i.base_price for i in relevant), Decimal("0")
            )
        if invoice.total_vat is None:
            invoice.total_vat = sum((i.tax_price for i in relevant), Decimal("0"))
        if invoice.total_price is None:
            invoice.total_price = invoice.total_without_vat + invoice.total_vat
    invoice.vat_check_ok, invoice.vat_difference = check_vat_items(
        items, invoice.total_price, invoice.currency
    )


_STAT_RANGES = {  # date_trunc unit -> (periods to return, days to look back)
    "day": (30, 29),
    "week": (12, 12 * 7),
    "month": (12, 366),
}


def stats(session: Session) -> InvoiceStats:
    """Invoice counts per day/week/month and by state for the dashboard."""
    result = InvoiceStats()
    today = get_datetime_utc().date()
    for unit, (periods, back) in _STAT_RANGES.items():
        since = today - timedelta(days=back)
        rows = (
            session.connection()
            .execute(
                sa_text(
                    f"SELECT date_trunc('{unit}', coalesce(issue_date,"  # noqa: S608 - unit is from _STAT_RANGES
                    " (created_at AT TIME ZONE 'UTC')::date)) AS period, state,"
                    " count(*) AS n FROM invoice"
                    " WHERE coalesce(issue_date, (created_at AT TIME ZONE 'UTC')::date)"
                    " >= :since GROUP BY 1, 2"
                ),
                {"since": since},
            )
            .all()
        )
        counts: dict[str, dict[str, int]] = {}
        for period, state, n in rows:
            key = _stat_period(period, unit)
            counts.setdefault(key, {})[str(state)] = int(n)
        buckets: list[InvoiceStatsBucket] = []
        cursor = today.replace(day=1) if unit == "month" else today
        if unit == "week":
            cursor -= timedelta(days=today.weekday())
        for _ in range(periods):
            key = _stat_period(cursor, unit)
            by_state = InvoiceStateCounts(**counts.get(key, {}))
            buckets.append(
                InvoiceStatsBucket(
                    period=key,
                    total=sum(counts.get(key, {}).values()),
                    by_state=by_state,
                )
            )
            if unit == "day":
                cursor -= timedelta(days=1)
            elif unit == "week":
                cursor -= timedelta(weeks=1)
            else:
                cursor = (cursor - timedelta(days=1)).replace(day=1)
        setattr(result, unit, list(reversed(buckets)))
    totals = (
        session.connection()
        .execute(sa_text("SELECT state, count(*) FROM invoice GROUP BY state"))
        .all()
    )
    result.totals = InvoiceStateCounts(**{str(state): int(n) for state, n in totals})
    return result


def _stat_period(moment: date | datetime, unit: str) -> str:
    day = moment.date() if isinstance(moment, datetime) else moment
    if unit == "month":
        return day.strftime("%Y-%m")
    if unit == "week":
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02d}"
    return day.isoformat()


def queue_reasons(session: Session, invoice: Invoice) -> list[str]:
    """Machine-readable keys for why an invoice stays in the task queue.

    The frontend translates ``queue.reason.*`` entries; a dynamic detail
    (error text, note) is appended after a ``|`` separator.
    """
    state = InvoiceState(invoice.state)
    reasons: list[str] = []
    if state in (InvoiceState.UPLOADED, InvoiceState.PARSING):
        reasons.append("queue.reason.processing")
    if invoice.parse_error:
        reasons.append(f"queue.reason.parse_error|{invoice.parse_error}")
    company = session.get(Company, invoice.company_id) if invoice.company_id else None
    if company is None:
        reasons.append("queue.reason.no_company")
    elif not company.is_active:
        reasons.append("queue.reason.company_inactive")
    if invoice.vat_check_ok is False:
        reasons.append("queue.reason.vat_mismatch")
    if company is not None and invoice.approved_at is None:
        reasons.append("queue.reason.awaiting_approval")
    if state is InvoiceState.EXPORT_FAILED:
        reasons.append(f"queue.reason.export_failed|{invoice.state_note or ''}")
    return reasons


def assert_approvable(session: Session, invoice: Invoice) -> None:
    company = session.get(Company, invoice.company_id) if invoice.company_id else None
    if company is None:
        raise ApprovalNotAllowedError("errors.approval_no_company")
    if not company.is_active:
        raise ApprovalNotAllowedError("errors.approval_company_inactive")
    if invoice.state in (InvoiceState.UPLOADED, InvoiceState.PARSING):
        raise ApprovalNotAllowedError("errors.approval_not_parsed")
    if invoice.vat_check_ok is False:
        raise ApprovalNotAllowedError("errors.approval_vat_mismatch")


def assign_company(
    session: Session,
    invoice: Invoice,
    company: Company,
    *,
    user: User | None = None,
) -> Invoice:
    invoice.company_id = company.id
    invoice.updated_at = get_datetime_utc()
    if InvoiceState(invoice.state) in (InvoiceState.UPLOADED, InvoiceState.PARSING):
        invoice.state = InvoiceState.NEEDS_REVIEW.value
    session.add(invoice)
    log_audit(
        session,
        "company_assigned",
        invoice=invoice,
        company=company,
        user=user,
        details={"company_name": company.name},
    )
    return invoice


def approve(session: Session, invoice: Invoice, *, user: User) -> Invoice:
    assert_approvable(session, invoice)
    invoice.approved_by_id = user.id
    invoice.approved_at = get_datetime_utc()
    invoice.updated_at = get_datetime_utc()
    session.add(invoice)
    log_audit(session, "approved", invoice=invoice, user=user)
    state = InvoiceState(invoice.state)
    if state in (InvoiceState.NEEDS_REVIEW, InvoiceState.EXPORT_FAILED):
        change_state(
            session,
            invoice,
            InvoiceState.READY_FOR_EXPORT,
            note="Schváleno uživatelem",
            user=user,
        )
    return invoice


def revoke_approval(session: Session, invoice: Invoice, *, user: User) -> Invoice:
    """User pulls an invoice back from the export queue (before import)."""
    if InvoiceState(invoice.state) is InvoiceState.EXPORTED:
        raise InvoiceLockedError("errors.invoice_locked")
    invoice.approved_at = None
    invoice.approved_by_id = None
    session.add(invoice)
    log_audit(session, "approval_revoked", invoice=invoice, user=user)
    if InvoiceState(invoice.state) in (
        InvoiceState.READY_FOR_EXPORT,
        InvoiceState.EXPORT_FAILED,
    ):
        change_state(
            session,
            invoice,
            InvoiceState.NEEDS_REVIEW,
            note="Zrušeno potvrzení importu",
            user=user,
        )
    return invoice


def record_export_result(
    session: Session,
    invoice: Invoice,
    *,
    success: bool,
    batch_ref: str | None = None,
    error: str | None = None,
) -> Invoice:
    if success:
        invoice.exported_at = get_datetime_utc()
        invoice.export_batch_ref = batch_ref
        session.add(invoice)
        change_state(
            session,
            invoice,
            InvoiceState.EXPORTED,
            note=f"Importováno do systému, dávka {batch_ref}",
        )
    else:
        invoice.export_batch_ref = batch_ref
        session.add(invoice)
        change_state(
            session, invoice, InvoiceState.EXPORT_FAILED, note=error or "export selhal"
        )
    return invoice


def delete_invoice(
    session: Session, invoice: Invoice, *, user: User | None = None
) -> None:
    if InvoiceState(invoice.state) not in DELETABLE_STATES:
        raise InvoiceLockedError("errors.invoice_locked")
    log_audit(
        session,
        "deleted",
        invoice=invoice,
        user=user,
        details={"filename": invoice.original_filename},
    )
    try:
        storage.get_storage().delete(invoice.storage_key)
    except FileNotFoundError, OSError:
        pass
    invoice_id = invoice.id
    session.delete(invoice)
    search_index.remove(invoice_id)


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------


def _vat_items_search_text(vat_items: list[Any] | None) -> str:
    """Flatten serialized VAT item amounts/rates into searchable text."""
    if not vat_items:
        return ""
    parts: list[str] = []
    for item in vat_items:
        values = item.values() if isinstance(item, dict) else []
        parts.extend(str(value) for value in values if value is not None)
    return " ".join(parts)


def update_search_vector(session: Session, invoice: Invoice) -> None:
    company = session.get(Company, invoice.company_id) if invoice.company_id else None
    fields = " ".join(
        str(part)
        for part in (
            invoice.invoice_number,
            invoice.supplier_name,
            invoice.supplier_ico,
            invoice.supplier_dic,
            invoice.supplier_bank_account,
            invoice.supplier_iban,
            " ".join(
                str(part.get("account") or "") + " " + str(part.get("iban") or "")
                for part in (invoice.supplier_bank_accounts or [])
            ),
            invoice.document_type,
            invoice.currency,
            invoice.issue_date,
            invoice.delivery_date,
            invoice.due_date,
            invoice.total_price,
            invoice.total_vat,
            invoice.total_without_vat,
            _vat_items_search_text(invoice.vat_items),
            company.name if company else "",
            invoice.original_filename,
        )
        if part
    )
    session.connection().execute(
        sa_text(
            """
            UPDATE invoice SET search_vector =
                setweight(to_tsvector('simple', coalesce(:fields, '')), 'A')
                || setweight(
                    to_tsvector('simple', coalesce(substring(ocr_text for 30000), '')),
                    'B'
                )
            WHERE id = :id
            """
        ),
        {"fields": fields, "id": invoice.id},
    )
    session.commit()
    session.expire(invoice)
    search_index.sync(session, invoice)


# ---------------------------------------------------------------------------
# Public projection
# ---------------------------------------------------------------------------


def to_public(session: Session, invoice: Invoice) -> InvoicePublic:
    public = InvoicePublic.model_validate(invoice, from_attributes=True)
    public.can_delete = InvoiceState(invoice.state) in DELETABLE_STATES
    public.queue_reasons = queue_reasons(session, invoice)
    dic, iban = invoice.supplier_dic, invoice.supplier_iban
    if invoice.company_id:
        company = session.get(Company, invoice.company_id)
        if company:
            from app.models import CompanyPublic

            public.company = CompanyPublic.model_validate(company)
            dic = company.dic or dic
            if not iban:
                accounts = company.bank_accounts
                iban = next((row.iban for row in accounts if row.iban), None)
    public.vat_rate_warnings = vat_rate_warnings(
        as_vat_items(invoice.vat_items), dic, iban
    )
    return public


# ---------------------------------------------------------------------------
# Background parsing pipeline
# ---------------------------------------------------------------------------


def apply_parsed(invoice: Invoice, parsed: ParsedInvoice) -> None:
    mapping = {
        "invoice_number": parsed.invoice_number,
        "supplier_name": parsed.supplier_name,
        "supplier_ico": parsed.supplier_ico,
        "supplier_dic": parsed.supplier_dic,
        "supplier_bank_account": parsed.supplier_bank_account,
        "supplier_iban": parsed.supplier_iban,
        "supplier_bank_accounts": (
            [row.model_dump(mode="json") for row in parsed.supplier_bank_accounts]
            if parsed.supplier_bank_accounts is not None
            else None
        ),
        "document_type": parsed.document_type,
        "issue_date": parsed.issue_date,
        "delivery_date": parsed.delivery_date,
        "due_date": parsed.due_date,
        "currency": parsed.currency,
        "total_price": parsed.total_price,
        "total_vat": parsed.total_vat,
        "total_without_vat": parsed.total_without_vat,
        "vat_items": vat_items_to_json(parsed.vat_items),
    }
    for field, value in mapping.items():
        if value is not None:
            setattr(invoice, field, value)
    invoice.llm_confidence = parsed.confidence


def process_invoice(invoice_id: uuid.UUID) -> None:
    """Background task entry point: parses one invoice in its own session."""
    with Session(engine) as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            return
        state = InvoiceState(invoice.state)
        if state not in (InvoiceState.UPLOADED, InvoiceState.PARSING):
            return
        if state is InvoiceState.UPLOADED:
            change_state(session, invoice, InvoiceState.PARSING)
            session.commit()
        started = time.monotonic()
        try:
            data = storage.get_storage().open(invoice.storage_key)
            text = parsing.extract_text(data, invoice.content_type)
            invoice.ocr_text = text or None
            image = data if invoice.content_type != "application/pdf" else None
            parsed = parsing.parse_invoice_text(
                text, image=image if not text.strip() else None
            )
        except Exception as exc:  # parsing backend unavailable, broken file, ...
            parsed = None
            invoice.parse_error = str(exc)[:255]
        else:
            invoice.parse_error = None
        logger.info(
            "Invoice %s parsed in %.1fs (ok=%s)",
            invoice_id,
            time.monotonic() - started,
            parsed is not None,
        )
        if parsed is not None:
            parsing.normalize_parsed(parsed)
            apply_parsed(invoice, parsed)
            recompute_derived(invoice)
            company = parsing.match_company(
                session,
                invoice.supplier_ico,
                invoice.supplier_dic,
                invoice.supplier_name,
            )
            if company is not None:
                invoice.company_id = company.id
                log_audit(
                    session, "company_auto_matched", invoice=invoice, company=company
                )
        else:
            if not invoice.parse_error:
                invoice.parse_error = "Nepodařilo se strukturovat obsah dokladu"
        change_state(
            session, invoice, InvoiceState.NEEDS_REVIEW, note=invoice.parse_error
        )
        session.commit()
        update_search_vector(session, invoice)


def reparse(session: Session, invoice: Invoice) -> Invoice:
    """Re-run parsing for an invoice stuck in NEEDS_REVIEW (manual trigger)."""
    if InvoiceState(invoice.state) not in (
        InvoiceState.NEEDS_REVIEW,
        InvoiceState.EXPORT_FAILED,
    ):
        raise InvalidTransitionError(
            "Opakovat parsování lze jen u zkontrolované faktury"
        )
    invoice.state = InvoiceState.PARSING.value
    invoice.parse_error = None
    session.add(invoice)
    session.commit()
    return invoice
