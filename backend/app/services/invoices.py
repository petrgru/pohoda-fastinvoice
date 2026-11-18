"""Invoice workflow service: state machine, approval, queue, FTS, audit.

State transitions are only performed here (never directly from routes),
see `InvoiceState` in app.models for the diagram.
"""

import logging
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, exists, func, or_
from sqlalchemy import text as sa_text
from sqlmodel import Session, col, select

from app.core.db import engine
from app.models import (
    DELETABLE_STATES,
    AuditLog,
    Invoice,
    InvoiceDuplicateHint,
    InvoicePublic,
    InvoiceState,
    InvoiceStateCounts,
    InvoiceStats,
    InvoiceStatsBucket,
    ParsedInvoice,
    RecipientCompany,
    Supplier,
    SupplierRegistryStatus,
    User,
    UserBasic,
    as_vat_items,
    check_vat_items,
    get_datetime_utc,
    normalize_document_number,
    normalize_supplier_ico,
    normalize_supplier_name,
    vat_items_to_json,
    vat_rate_warnings,
)
from app.services import parsing, recipient_access, registry, search_index, storage

logger = logging.getLogger(__name__)

# Transitions allowed by the workflow.
_ALLOWED_TRANSITIONS: dict[InvoiceState, set[InvoiceState]] = {
    InvoiceState.UPLOADED: {InvoiceState.PARSING},
    InvoiceState.PARSING: {InvoiceState.NEEDS_REVIEW},
    InvoiceState.NEEDS_REVIEW: {InvoiceState.READY_FOR_EXPORT, InvoiceState.PARSING},
    InvoiceState.READY_FOR_EXPORT: {
        InvoiceState.PROCESSING,
        InvoiceState.EXPORTED,
        InvoiceState.EXPORT_FAILED,
        InvoiceState.NEEDS_REVIEW,
    },
    InvoiceState.PROCESSING: {
        InvoiceState.EXPORTED,
        InvoiceState.EXPORT_FAILED,
        InvoiceState.READY_FOR_EXPORT,
    },
    InvoiceState.EXPORT_FAILED: {
        InvoiceState.NEEDS_REVIEW,
        InvoiceState.READY_FOR_EXPORT,
        InvoiceState.PROCESSING,
        InvoiceState.EXPORTED,
    },
    InvoiceState.EXPORTED: set(),
}

# automatic connector import attempts before a human must re-queue
EXPORT_MAX_RETRIES = 3

# Duplicate detection compares the document number of a *document*, not the
# bytes of a file: rescanning the same paper invoice produces different PDF
# bytes every time, so a file hash alone never catches it. States whose
# duplicate has already reached (or entered) the accounting system block the
# approval of a second copy – importing the same document twice would be a
# real posting error.
DUPLICATE_BLOCKING_STATES = {
    InvoiceState.READY_FOR_EXPORT,
    InvoiceState.PROCESSING,
    InvoiceState.EXPORTED,
}


class InvoiceLockedError(PermissionError):
    """Raised when a record can no longer be modified or deleted."""


class InvalidTransitionError(ValueError):
    pass


class ApprovalNotAllowedError(ValueError):
    pass


class DuplicateInvoiceError(ValueError):
    """The same document number is already approved (or imported) elsewhere."""


def log_audit(
    session: Session,
    action: str,
    *,
    invoice: Invoice | None = None,
    company: RecipientCompany | None = None,
    user: User | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            invoice_id=invoice.id if invoice else None,
            recipient_company_id=company.id if company else None,
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
    # state is a Meilisearch filter attribute, keep the mirror current
    search_index.sync(session, invoice)
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


def stats(
    session: Session,
    visible_company_ids: list[uuid.UUID] | None = None,
    user_id: uuid.UUID | None = None,
    only_company_id: uuid.UUID | None = None,
) -> InvoiceStats:
    """Invoice counts per day/week/month and by state for the dashboard.

    ``visible_company_ids=None`` counts everything (superuser); otherwise only
    rows of those companies plus the given user's own unassigned uploads.
    """
    result = InvoiceStats()
    scope = ""
    scope_params: dict[str, Any] = {}
    if visible_company_ids is not None:
        scope = " AND (recipient_company_id = ANY(:visible_ids) OR uploaded_by_id = :scope_user)"
        scope_params = {"visible_ids": visible_company_ids, "scope_user": user_id}
    if only_company_id is not None:
        scope += " AND recipient_company_id = :only_company"
        scope_params["only_company"] = only_company_id
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
                    f" >= :since{scope} GROUP BY 1, 2"
                ),
                {"since": since, **scope_params},
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
        .execute(
            sa_text(
                f"SELECT state, count(*) FROM invoice WHERE 1=1{scope} GROUP BY state"  # noqa: S608 - scope is built above from UUIDs
            ),
            scope_params,
        )
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


def missing_required_dates(invoice: Invoice) -> list[str]:
    """Machine keys of the missing mandatory dates of a tax document."""
    if str(invoice.document_type or "invoice") not in ("invoice", "other"):
        return []
    return [
        key
        for field, key in (
            (invoice.issue_date, "issueDate"),
            (invoice.due_date, "dueDate"),
            (invoice.delivery_date, "taxDate"),
        )
        if field is None
    ]


def queue_reasons(
    session: Session, invoice: Invoice, *, duplicates: list[Invoice] | None = None
) -> list[str]:
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
    company = (
        session.get(RecipientCompany, invoice.recipient_company_id)
        if invoice.recipient_company_id
        else None
    )
    if company is None:
        reasons.append("queue.reason.no_company")
    elif not company.is_active:
        reasons.append("queue.reason.company_inactive")
    if invoice.vat_check_ok is False:
        reasons.append("queue.reason.vat_mismatch")
    reasons.extend(
        f"queue.reason.missing{key}" for key in missing_required_dates(invoice)
    )
    if duplicates is None:
        duplicates = find_duplicates(session, invoice)
    if duplicates:
        reasons.append(f"queue.reason.duplicate|{duplicate_detail(duplicates)}")
    if company is not None and invoice.approved_at is None:
        reasons.append("queue.reason.awaiting_approval")
    if state is InvoiceState.EXPORT_FAILED:
        detail = invoice.erp_error_message or invoice.state_note or ""
        reasons.append(f"queue.reason.export_failed|{detail}")
    elif state is InvoiceState.PROCESSING:
        reasons.append("queue.reason.export_in_progress")
    if state in (InvoiceState.READY_FOR_EXPORT, InvoiceState.EXPORT_FAILED):
        from app.services import account_integrity

        blocked = registry.export_block_reason(
            account_integrity.find_supplier(session, invoice)
        )
        if blocked:
            reasons.append(f"queue.reason.{blocked}")
    return reasons


def find_duplicates(session: Session, invoice: Invoice) -> list[Invoice]:
    """Other live documents with the same number from the same supplier.

    The recipient company is *not* part of the key – one supplier's document
    belongs to exactly one of our companies, so a second copy anywhere (even
    under another company) is precisely the error worth catching. Matching
    uses normalized values: separators and case in the number, case,
    diacritics and legal-form suffixes in the supplier name.
    """
    number = normalize_document_number(invoice.invoice_number)
    if not number:
        return []
    candidates = session.exec(
        select(Invoice)
        .where(
            col(Invoice.id) != invoice.id,
            func.regexp_replace(
                func.lower(Invoice.invoice_number), "[^a-z0-9]", "", "g"
            )
            == number,
        )
        .order_by(col(Invoice.created_at).asc())
    ).all()
    ico = normalize_supplier_ico(invoice.supplier_ico)
    name = normalize_supplier_name(invoice.supplier_name)
    duplicates = []
    for other in candidates:
        other_ico = normalize_supplier_ico(other.supplier_ico)
        if ico and other_ico:
            if ico != other_ico:
                continue
        elif not name or normalize_supplier_name(other.supplier_name) != name:
            continue
        duplicates.append(other)
    return duplicates


def blocking_duplicates(session: Session, invoice: Invoice) -> list[Invoice]:
    """Duplicates already approved for (or through) the export."""
    return [
        other
        for other in find_duplicates(session, invoice)
        if InvoiceState(other.state) in DUPLICATE_BLOCKING_STATES
    ]


def duplicate_detail(duplicates: list[Invoice]) -> str:
    """Human-readable list for warnings: file name and date, newest detail."""
    return ", ".join(
        f"{item.original_filename}"
        f" ({(item.created_at or get_datetime_utc()):%d.%m.%Y %H:%M})"
        for item in duplicates[:3]
    ) + (" …" if len(duplicates) > 3 else "")


def assert_approvable(session: Session, invoice: Invoice) -> None:
    company = (
        session.get(RecipientCompany, invoice.recipient_company_id)
        if invoice.recipient_company_id
        else None
    )
    if company is None:
        raise ApprovalNotAllowedError("errors.approval_no_company")
    if not company.is_active:
        raise ApprovalNotAllowedError("errors.approval_company_inactive")
    if invoice.state in (InvoiceState.UPLOADED, InvoiceState.PARSING):
        raise ApprovalNotAllowedError("errors.approval_not_parsed")
    if InvoiceState(invoice.state) is InvoiceState.PROCESSING:
        raise ApprovalNotAllowedError("errors.approval_in_progress")
    if invoice.vat_check_ok is False:
        raise ApprovalNotAllowedError("errors.approval_vat_mismatch")
    missing = missing_required_dates(invoice)
    if missing:
        raise ApprovalNotAllowedError(
            "errors.approvalMissingDates|" + ",".join(missing)
        )
    blocking = blocking_duplicates(session, invoice)
    if blocking:
        raise DuplicateInvoiceError(
            f"errors.duplicate_approved|{duplicate_detail(blocking)}"
        )


def assign_company(
    session: Session,
    invoice: Invoice,
    company: RecipientCompany,
    *,
    user: User | None = None,
) -> Invoice:
    invoice.recipient_company_id = company.id
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


def replace_duplicates(session: Session, invoice: Invoice, *, user: User) -> None:
    """Drop the approved copies this document is meant to replace.

    Used when a user re-scanned an invoice that is already waiting for the
    import: the newer record wins, the older ones are deleted. A duplicate
    that has already been imported is locked and stays – approving a second
    copy would double-book it, so nothing is deleted and the caller is told.
    """
    blocking = blocking_duplicates(session, invoice)
    imported = [
        item for item in blocking if InvoiceState(item.state) is InvoiceState.EXPORTED
    ]
    if imported:
        raise DuplicateInvoiceError(
            f"errors.duplicate_of_exported|{duplicate_detail(imported)}"
        )
    for other in blocking:
        delete_invoice(session, other, user=user)
        log_audit(
            session,
            "duplicate_replaced",
            invoice=invoice,
            user=user,
            details={"replaced": other.original_filename},
        )


def approve(
    session: Session, invoice: Invoice, *, user: User, delete_duplicates: bool = False
) -> Invoice:
    if delete_duplicates:
        replace_duplicates(session, invoice, user=user)
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
    release_export_claim(session, invoice, reason="Odzvážené schválení uživatelem")
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
        invoice.erp_error_message = None
        invoice.export_claimed_at = None
        session.add(invoice)
        change_state(
            session,
            invoice,
            InvoiceState.EXPORTED,
            note=(
                f"Importováno do systému, dávka {batch_ref}"
                if batch_ref
                else "Importováno do systému"
            ),
        )
    else:
        invoice.export_batch_ref = batch_ref
        invoice.erp_error_message = (error or "export selhal")[:500]
        invoice.export_claimed_at = None
        session.add(invoice)
        change_state(
            session, invoice, InvoiceState.EXPORT_FAILED, note=error or "export selhal"
        )
    return invoice


def release_export_claim(session: Session, invoice: Invoice, *, reason: str) -> Invoice:
    """Return an invoice claimed by a connector back to the export queue."""
    if InvoiceState(invoice.state) is not InvoiceState.PROCESSING:
        return invoice
    invoice.export_claimed_at = None
    session.add(invoice)
    return change_state(session, invoice, InvoiceState.READY_FOR_EXPORT, note=reason)


def _supplier_registry_blocked() -> Any:
    """EXISTS clause: invoice whose supplier is not registry-confirmed yet.

    PENDING/NOT_FOUND hold the export until ARES/VIES confirm the subject;
    a confirmed insolvency blocks unless an admin released it manually.
    Invoices without a registry row (or a NOT_CHECKABLE supplier) pass.
    """
    return exists().where(
        col(Invoice.supplier_ico) == Supplier.ico,
        or_(
            col(Supplier.registry_status).in_(
                [
                    SupplierRegistryStatus.PENDING.value,
                    SupplierRegistryStatus.NOT_FOUND.value,
                ]
            ),
            and_(
                col(Supplier.in_insolvency) == True,  # noqa: E712
                col(Supplier.export_confirmed_manually) == False,  # noqa: E712
            ),
        ),
    )


def claim_export_batch(
    session: Session, company_id: uuid.UUID, *, limit: int
) -> list[Invoice]:
    """Hand the connector the next batch of invoices ready for import.

    Waits are ``ready_for_export`` or failed ones below the retry limit; the
    rows are locked with SKIP LOCKED so a second connector instance simply
    picks different records. Invoices of suppliers still waiting for registry
    confirmation (or in an unresolved insolvency) stay behind the gate.
    """
    rows = session.exec(
        select(Invoice)
        .where(
            Invoice.recipient_company_id == company_id,
            or_(
                Invoice.state == InvoiceState.READY_FOR_EXPORT.value,
                and_(
                    Invoice.state == InvoiceState.EXPORT_FAILED.value,
                    Invoice.export_retry_count < EXPORT_MAX_RETRIES,
                ),
            ),
            ~_supplier_registry_blocked(),
        )
        .order_by(col(Invoice.created_at).asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    claimed = list(rows)
    now = get_datetime_utc()
    for invoice in claimed:
        change_state(
            session, invoice, InvoiceState.PROCESSING, note="Vyzvednuto konektorem"
        )
        invoice.export_claimed_at = now
        session.add(invoice)
    session.commit()
    return claimed


def release_stale_export_claims(
    session: Session, *, ttl: timedelta = timedelta(minutes=10)
) -> int:
    """Re-queue claims whose connector never reported a result (crash mid-batch)."""
    cutoff = get_datetime_utc() - ttl
    stuck = session.exec(
        select(Invoice).where(
            Invoice.state == InvoiceState.PROCESSING.value,
            col(Invoice.export_claimed_at) < cutoff,
        )
    ).all()
    for invoice in stuck:
        release_export_claim(
            session,
            invoice,
            reason="Konektor nedokončil přenos (timeout, vráceno do fronty)",
        )
    session.commit()
    if stuck:
        logger.info("released %d stale connector claim(s)", len(stuck))
    return len(stuck)


def requeue_export(
    session: Session, invoice: Invoice, *, user: User | None = None
) -> Invoice:
    """User forces another connector attempt (resets the retry counter)."""
    state = InvoiceState(invoice.state)
    if state is InvoiceState.EXPORTED:
        raise InvoiceLockedError("errors.invoice_locked")
    if state is InvoiceState.PROCESSING:
        release_export_claim(session, invoice, reason="Znovu zařazeno uživatelem")
        return invoice
    if state is not InvoiceState.EXPORT_FAILED:
        raise InvalidTransitionError(f"{state.value} -> requeue")
    invoice.export_retry_count = 0
    invoice.erp_error_message = None
    invoice.export_claimed_at = None
    session.add(invoice)
    return change_state(
        session,
        invoice,
        InvoiceState.READY_FOR_EXPORT,
        note="Znovu zařazeno uživatelem",
        user=user,
    )


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
    company = (
        session.get(RecipientCompany, invoice.recipient_company_id)
        if invoice.recipient_company_id
        else None
    )
    fields = " ".join(
        str(part)
        for part in (
            invoice.invoice_number,
            invoice.supplier_name,
            invoice.supplier_ico,
            invoice.supplier_dic,
            invoice.supplier_bank_account,
            invoice.supplier_iban,
            invoice.supplier_email,
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
    duplicates = find_duplicates(session, invoice)
    public.queue_reasons = queue_reasons(session, invoice, duplicates=duplicates)
    public.duplicates = [
        InvoiceDuplicateHint(
            id=other.id,
            original_filename=other.original_filename,
            invoice_number=other.invoice_number,
            state=other.state,
            created_at=other.created_at,
            recipient_company_name=(
                session.get(RecipientCompany, other.recipient_company_id).name
                if other.recipient_company_id
                and session.get(RecipientCompany, other.recipient_company_id)
                else None
            ),
            approved=InvoiceState(other.state) in DUPLICATE_BLOCKING_STATES,
        )
        for other in duplicates
    ]
    for attr, user_id in (
        ("uploaded_by", invoice.uploaded_by_id),
        ("approved_by", invoice.approved_by_id),
    ):
        if user_id:
            actor = session.get(User, user_id)
            if actor:
                setattr(
                    public,
                    attr,
                    UserBasic(
                        id=actor.id, full_name=actor.full_name, email=actor.email
                    ),
                )
    dic, iban = invoice.supplier_dic, invoice.supplier_iban
    if invoice.recipient_company_id:
        company = session.get(RecipientCompany, invoice.recipient_company_id)
        if company:
            from app.models import RecipientCompanyPublic

            public.company = RecipientCompanyPublic.model_validate(company)
            dic = company.dic or dic
            if not iban:
                accounts = company.bank_accounts
                iban = next((row.iban for row in accounts if row.iban), None)
    public.vat_rate_warnings = vat_rate_warnings(
        as_vat_items(invoice.vat_items), dic, iban
    )
    from app.services import account_integrity

    public.account_matches_document = account_integrity.matches_document(invoice)
    public.document_bank_accounts = (
        sorted(account_integrity.document_accounts(invoice) or []) or None
    )
    public.account_matches_supplier = account_integrity.matches_supplier(
        session, invoice
    )
    supplier = account_integrity.find_supplier(session, invoice)
    if supplier is not None:
        confirmed = sorted(
            account
            for account, ok in account_integrity.known_accounts(supplier).items()
            if ok
        )
        public.supplier_confirmed_accounts = confirmed or None
    public.export_block_reason = registry.export_block_reason(supplier)
    return public


# ---------------------------------------------------------------------------
# Background parsing pipeline
# ---------------------------------------------------------------------------


def assign_recipient_company(session: Session, invoice: Invoice) -> None:
    """Attach the uploader's company the parsed recipient matches.

    An explicit choice made at upload (`recipient_company_id` already set) always wins.
    Without a recognised recipient a member with exactly one company gets it
    assigned; anything else stays unassigned for the review screen.
    """
    if invoice.recipient_company_id is not None:
        return
    user = session.get(User, invoice.uploaded_by_id) if invoice.uploaded_by_id else None
    if user is None:
        return
    company = parsing.match_recipient_company(
        recipient_access.candidate_companies(session, user),
        invoice.recipient_ico,
        invoice.recipient_dic,
        invoice.recipient_name,
    )
    action = "company_auto_matched"
    if company is None:
        company = recipient_access.sole_company_for(session, user)
        action = "company_default_assigned"
    if company is None:
        return
    invoice.recipient_company_id = company.id
    log_audit(session, action, invoice=invoice, company=company)


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
        "recipient_name": parsed.recipient_name,
        "recipient_ico": parsed.recipient_ico,
        "recipient_dic": parsed.recipient_dic,
        "document_type": parsed.document_type,
        "supplier_email": parsed.supplier_email,
        "payment_method": parsed.payment_method,
        "issue_date": parsed.issue_date,
        "delivery_date": parsed.delivery_date,
        "due_date": parsed.due_date,
        "kh_date": parsed.kh_date,
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
    # the document baseline for fraud detection – written once, never rewritten
    if invoice.parsed_snapshot is None:
        invoice.parsed_snapshot = {
            field: mapping.get(field)
            for field in (
                "supplier_name",
                "supplier_ico",
                "supplier_dic",
                "supplier_bank_account",
                "supplier_iban",
                "supplier_bank_accounts",
            )
        }


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
            image: bytes | None = None
            if not text.strip():
                if invoice.content_type == "application/pdf":
                    # scanned pages have no text layer - show the vision model
                    # the first page, the way an uploaded image would be sent
                    image = parsing.render_pdf_first_page(data)
                else:
                    image = data
            parsed = parsing.parse_invoice_text(text, image=image)
        except parsing.TransientLLMError:
            # LLM unreachable - keep the document in PARSING and let the
            # queue retry; inline mode handles this in tasks.run_parse_inline
            raise
        except Exception as exc:  # broken file, OCR failure, ...
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
        try:
            if parsed is not None:
                parsing.normalize_parsed(parsed)
                apply_parsed(invoice, parsed)
                recompute_derived(invoice)
                assign_recipient_company(session, invoice)
                # lazy import: account_integrity uses log_audit from this module
                from app.services import account_integrity

                try:
                    account_integrity.upsert_from_invoice(session, invoice)
                except Exception:
                    # a missing supplier account ledger is no reason to drop
                    # the parsed document out of the queue
                    logger.exception(
                        "Invoice %s: supplier account ledger failed", invoice_id
                    )
            elif not invoice.parse_error:
                invoice.parse_error = "Nepodařilo se strukturovat obsah dokladu"
        except Exception as exc:
            # never leave the invoice in PARSING - the UI would wait forever
            logger.exception("Invoice %s: storing the parse result failed", invoice_id)
            session.rollback()
            invoice = session.get(Invoice, invoice_id)
            invoice.parse_error = f"Chyba při ukládání výsledku parsování: {exc}"[:255]
        change_state(
            session, invoice, InvoiceState.NEEDS_REVIEW, note=invoice.parse_error
        )
        session.commit()
        update_search_vector(session, invoice)


def fail_transient_parse(invoice_id: uuid.UUID, reason: str) -> None:
    """Terminal handling for a document whose parsing kept failing on I/O.

    Used when no retry will come anymore (inline mode, or the queue gave up):
    instead of leaving the document in PARSING forever it goes to the review
    screen with the failure recorded.
    """
    with Session(engine) as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None or InvoiceState(invoice.state) is not InvoiceState.PARSING:
            return
        invoice.parse_error = f"LLM se nepodařilo dokončit: {reason}"[:255]
        change_state(
            session, invoice, InvoiceState.NEEDS_REVIEW, note=invoice.parse_error
        )
        session.commit()


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


def recover_stuck_invoices() -> int:
    """Re-queue documents whose background parsing was cut off by a restart.

    Without this they would wait in UPLOADED/PARSING forever: the background
    task that was working on them is gone and nothing else picks them up.
    Returns the number of documents put back into the queue.
    """
    with Session(engine) as session:
        stuck = session.exec(
            select(Invoice).where(
                Invoice.state.in_(  # type: ignore[attr-defined]
                    [InvoiceState.UPLOADED.value, InvoiceState.PARSING.value]
                )
            )
        ).all()
        invoice_ids = [invoice.id for invoice in stuck]
        for invoice in stuck:
            invoice.state = InvoiceState.UPLOADED.value
            session.add(invoice)
        session.commit()
    if invoice_ids:
        logger.info("re-queueing %d interrupted document(s)", len(invoice_ids))
        threading.Thread(
            target=_process_after_restart, args=(invoice_ids,), daemon=True
        ).start()
    return len(invoice_ids)


def _process_after_restart(invoice_ids: list[uuid.UUID]) -> None:
    # lazy import: app.tasks imports this module
    from app import tasks

    for invoice_id in invoice_ids:
        try:
            tasks.enqueue_invoice_parse_detached(invoice_id)
        except Exception:
            logger.exception("Invoice %s: recovery parsing failed", invoice_id)
