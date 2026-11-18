"""Procrastinate task queue: background parsing, email delivery, maintenance.

The queue lives in the app's own PostgreSQL database (SKIP LOCKED +
LISTEN/NOTIFY), so running it adds only a worker process - no broker.
``QUEUE_MODE=inline`` (development, tests) runs the same task bodies inside
the API process, so callers do not need to care which mode is active.

Workers are started with::

    procrastinate --app app.tasks.app worker --queues parse --concurrency 2
    procrastinate --app app.tasks.app worker --queues email,maintenance
"""

import logging
import re
import uuid
from datetime import timedelta
from typing import Any

import psycopg
import structlog
from asgi_correlation_id import correlation_id
from fastapi import BackgroundTasks
from procrastinate import App, PsycopgConnector, RetryStrategy, exceptions
from sqlalchemy import and_, or_
from sqlmodel import Session, col, select
from structlog.contextvars import bind_contextvars, clear_contextvars

from app import utils as email_utils
from app.core.config import settings
from app.core.db import engine
from app.core.logging import configure_logging
from app.models import (
    Invoice,
    InvoiceState,
    Supplier,
    SupplierRegistryStatus,
    get_datetime_utc,
)
from app.services import adis, parsing, registry
from app.services import invoices as invoice_service

logger = logging.getLogger(__name__)
slog = structlog.get_logger("app.tasks")

# the worker/CLI entry point shares the API's logging pipeline
configure_logging()  # psycopg conninfo does not understand SQLAlchemy's "+psycopg" driver suffix
_DSN = re.sub(r"^postgresql\+psycopg", "postgresql", str(settings.DATABASE_URL))

app = App(connector=PsycopgConnector(conninfo=_DSN))
# PsycopgConnector exposes the full sync surface as well: deferring from the
# synchronous FastAPI handlers goes through it, the worker/CLI use its async
# side (the procrastinate CLI requires an async connector).

# parse job may burn several LLM timeouts per attempt before it gives up
_PARSE_STALE_SECONDS = (
    settings.LLM_TIMEOUT_SECONDS + settings.LLM_STRUCTURED_TIMEOUT_SECONDS
) * settings.PARSE_MAX_RETRIES + 60

_app_opened = False


def _ensure_open() -> None:
    global _app_opened
    if not _app_opened:
        app.open()
        _app_opened = True


# --- tasks -------------------------------------------------------------------


@app.task(
    name="parse_invoice",
    queue="parse",
    retry=RetryStrategy(
        max_attempts=settings.PARSE_MAX_RETRIES,
        exponential_wait=10,
        retry_exceptions=(parsing.TransientLLMError,),
    ),
)
def parse_invoice_task(invoice_id: str, request_id: str | None = None) -> None:
    # the worker has no request context: bind what the enqueueing request knew
    clear_contextvars()
    bind_contextvars(
        invoice_id=invoice_id, **({"correlation_id": request_id} if request_id else {})
    )
    slog.debug("parse_invoice task started")
    try:
        invoice_service.process_invoice(uuid.UUID(invoice_id))
    finally:
        clear_contextvars()


@app.task(
    name="deliver_email",
    queue="email",
    retry=RetryStrategy(max_attempts=settings.EMAIL_MAX_RETRIES, exponential_wait=30),
)
def deliver_email_task(email_to: str, subject: str, html_content: str) -> None:
    # module-attribute lookup keeps tests able to patch app.utils.send_email
    email_utils.send_email(
        email_to=email_to, subject=subject, html_content=html_content
    )


@app.periodic(cron="*/5 * * * *")
@app.task(name="release_stale_export_claims", queue="maintenance")
def release_stale_export_claims_job(timestamp: int) -> None:
    logger.info("release_stale_export_claims pass @ %s", timestamp)
    with Session(engine) as session:
        invoice_service.release_stale_export_claims(session)


@app.periodic(cron="*/5 * * * *")
@app.task(name="rescue_stalled_parsing", queue="maintenance")
def rescue_stalled_parsing_job(timestamp: int) -> None:
    """Surface documents whose parse job died with the worker or is done.

    An invoice may only sit in PARSING while a parse job is queued or running
    for it; once that is not the case (job failed after retries, worker
    killed) the document is put up for manual review instead of waiting
    forever.
    """
    logger.info("rescue_stalled_parsing pass @ %s", timestamp)
    cutoff = get_datetime_utc() - timedelta(seconds=_PARSE_STALE_SECONDS)
    with Session(engine) as session:
        stalled = session.exec(
            select(Invoice).where(
                Invoice.state == InvoiceState.PARSING.value,
                col(Invoice.updated_at).is_not(None),
                col(Invoice.updated_at) < cutoff,
            )
        ).all()
    for invoice in stalled:
        job = app.job_manager.list_jobs(
            task="parse_invoice", lock=str(invoice.id), status="doing"
        ) or app.job_manager.list_jobs(
            task="parse_invoice", lock=str(invoice.id), status="todo"
        )
        if not job:
            invoice_service.fail_transient_parse(
                invoice.id, "parsování nedokončeno (LLM nedostupný, vyčerpány pokusy)"
            )


@app.task(name="check_supplier_registry", queue="maintenance")
def check_supplier_registry_task(
    supplier_id: str, request_id: str | None = None
) -> None:
    """First-time verification of one supplier, right after it is registered."""
    clear_contextvars()
    bind_contextvars(
        supplier_id=supplier_id,
        **({"correlation_id": request_id} if request_id else {}),
    )
    try:
        with Session(engine) as session:
            supplier = session.get(Supplier, uuid.UUID(supplier_id))
            if supplier is None:
                return
            try:
                registry.refresh_supplier(supplier)
            except registry.RegistryUnavailableError:
                registry.schedule_retry(supplier)
            except ValueError:
                logger.exception("registry lookup for %s", supplier_id)
            session.add(supplier)
            session.commit()
    finally:
        clear_contextvars()


@app.periodic(cron="*/10 * * * *")
@app.task(name="registry_checks", queue="maintenance")
def registry_checks_job(timestamp: int) -> None:
    """Verify due suppliers against ARES/VIES; hold exports until confirmed.

    Picks suppliers whose next check is due (never-checked, backing off, or
    past the insolvency re-check window). A registry outage leaves the status
    untouched - the supplier keeps its PENDING gate and retries on the next
    pass, so a flaky registry never wrongly releases or blocks an invoice.
    """
    if not settings.REGISTRY_ENABLED:
        return
    logger.info("registry_checks pass @ %s", timestamp)
    now = get_datetime_utc()
    fresh = and_(
        col(Supplier.registry_status).in_(
            [
                SupplierRegistryStatus.PENDING.value,
                SupplierRegistryStatus.NOT_FOUND.value,
            ]
        ),
        or_(
            col(Supplier.registry_next_check_at).is_(None),
            col(Supplier.registry_next_check_at) <= now,
        ),
    )
    recheck = and_(
        col(Supplier.registry_status) == SupplierRegistryStatus.VERIFIED.value,
        col(Supplier.registry_next_check_at) <= now,
    )
    with Session(engine) as session:
        due = session.exec(select(Supplier).where(or_(fresh, recheck)).limit(100)).all()
        for supplier in due:
            try:
                registry.refresh_supplier(supplier)
            except registry.RegistryUnavailableError:
                registry.schedule_retry(supplier)
            except ValueError:
                logger.exception("registry lookup for %s", supplier.id)
            session.add(supplier)
        session.commit()


@app.periodic(cron="10 3 * * *")
@app.task(name="adis_reliability", queue="maintenance")
def adis_reliability_job(timestamp: int) -> None:
    """Nightly ADIS sweep: unreliable-payer flag + published accounts.

    Informational only (never gates the export), so a service problem is
    logged and left for the next pass.
    """
    if not settings.REGISTRY_ENABLED:
        return
    logger.info("adis_reliability pass @ %s", timestamp)
    with Session(engine) as session:
        payers = session.exec(
            select(Supplier).where(
                col(Supplier.dic).is_not(None),
                col(Supplier.registry_status) == SupplierRegistryStatus.VERIFIED.value,
            )
        ).all()
        by_dic = {s.dic.upper(): s for s in payers if s.dic}
        for start in range(0, len(by_dic), adis.MAX_BATCH):
            chunk = list(by_dic)[start : start + adis.MAX_BATCH]
            try:
                results = adis.check_batch(chunk)
            except adis.AdisUnavailableError:
                logger.warning("ADIS unreachable, deferring to next pass")
                return
            for dic, entry in results.items():
                supplier = by_dic.get(dic)
                if supplier is None:
                    continue
                supplier.unreliable_payer = bool(entry.get("unreliable"))
                published = entry.get("published_accounts") or []
                data = supplier.registry_data or {}
                data["published_accounts"] = published
                supplier.registry_data = data
                session.add(supplier)
        session.commit()


# housekeeping (once a day): forget finished queue jobs older than a week so
# the queue tables don't grow with the invoice history
app.periodic(
    cron="0 4 * * *",
    queue="maintenance",
    periodic_id="prune",
    task_kwargs={
        "max_hours": 168,
        "remove_failed": True,
        "remove_cancelled": True,
        "remove_aborted": True,
    },
)(app.tasks["procrastinate.builtin_tasks.remove_old_jobs"])


# --- enqueue helpers ----------------------------------------------------------


def run_parse_inline(invoice_id: uuid.UUID) -> None:
    """Parse synchronously (inline mode); a dead LLM surfaces for review."""
    bind_contextvars(invoice_id=str(invoice_id))
    try:
        invoice_service.process_invoice(invoice_id)
    except parsing.TransientLLMError as exc:
        invoice_service.fail_transient_parse(invoice_id, str(exc))


def _run_or_defer_later(
    invoice_id: uuid.UUID, background_tasks: BackgroundTasks | None
) -> None:
    if background_tasks is not None:
        background_tasks.add_task(run_parse_inline, invoice_id)
    else:
        run_parse_inline(invoice_id)


def _defer_parse(invoice_id: uuid.UUID, connection: Any = None) -> None:
    """Insert a parse job; a pending duplicate for the same invoice is fine."""
    _ensure_open()
    task_kwargs: dict[str, str] = {"invoice_id": str(invoice_id)}
    # carry the enqueueing request's correlation id across the queue boundary
    if correlation_id.get(None):
        task_kwargs["request_id"] = str(correlation_id.get())
    try:
        if connection is not None:
            parse_invoice_task.configure(
                lock=str(invoice_id),
                queueing_lock=str(invoice_id),
                connection=connection,
            ).defer(**task_kwargs)
        else:
            parse_invoice_task.configure(
                lock=str(invoice_id), queueing_lock=str(invoice_id)
            ).defer(**task_kwargs)
    except exceptions.AlreadyEnqueued:
        logger.info("invoice %s is already queued for parsing", invoice_id)


def _session_psycopg_connection(session: Session) -> Any:
    """The raw psycopg3 connection a SQLModel session is transacting on."""
    sa_connection = session.connection()
    return getattr(sa_connection, "dbapi_connection", sa_connection.connection)


def enqueue_invoice_parse(
    session: Session,
    invoice_id: uuid.UUID,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """Queue parsing of a freshly created invoice.

    In queue mode the job INSERT runs on the caller's open transaction, so
    the invoice row, its audit log and the queue entry commit atomically
    (transactional outbox). Inline mode keeps the pre-queue behaviour and
    runs after the response via BackgroundTasks.
    """
    if settings.QUEUE_MODE == "inline":
        _run_or_defer_later(invoice_id, background_tasks)
        return
    try:
        _defer_parse(invoice_id, connection=_session_psycopg_connection(session))
    except psycopg.Error:
        # the session's driver connection could not be reused (e.g. a
        # transaction-mode pooler): a standalone defer still loses at most
        # the crash window covered by the rescue job
        logger.exception("could not defer on the session connection")
        _defer_parse(invoice_id)


def enqueue_invoice_parse_detached(
    invoice_id: uuid.UUID,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> None:
    """Re-queue parsing without an ambient transaction (recovery, reparse)."""
    if settings.QUEUE_MODE == "inline":
        _run_or_defer_later(invoice_id, background_tasks)
        return
    _defer_parse(invoice_id)


def enqueue_email(*, email_to: str, subject: str, html_content: str) -> None:
    """Deliver one email through the queue (synchronously in inline mode)."""
    if not settings.emails_enabled:
        logger.info("emails disabled, skipping the message")
        return
    if settings.QUEUE_MODE == "inline":
        email_utils.send_email(
            email_to=email_to, subject=subject, html_content=html_content
        )
        return
    _ensure_open()
    deliver_email_task.defer(
        email_to=email_to, subject=subject, html_content=html_content
    )


def enqueue_supplier_check(session: Session, supplier: Supplier) -> None:
    """Start the first registry verification of a freshly registered supplier.

    Called from the parse pipeline, so in inline mode the check already runs
    off the request path; in queue mode it is a transactional defer deduped
    by the supplier id.
    """
    if not settings.REGISTRY_ENABLED or not (supplier.ico or supplier.dic):
        return
    if settings.QUEUE_MODE == "inline":
        try:
            registry.refresh_supplier(supplier)
        except registry.RegistryUnavailableError, ValueError:
            registry.schedule_retry(supplier)
        session.add(supplier)
        return
    _ensure_open()
    task_kwargs: dict[str, str] = {"supplier_id": str(supplier.id)}
    if correlation_id.get(None):
        task_kwargs["request_id"] = str(correlation_id.get())
    try:
        check_supplier_registry_task.configure(
            queueing_lock=str(supplier.id),
            connection=_session_psycopg_connection(session),
        ).defer(**task_kwargs)
    except exceptions.AlreadyEnqueued:
        logger.info("supplier %s is already queued for a registry check", supplier.id)
    except psycopg.Error:
        logger.exception("could not defer the registry check on the session")
        check_supplier_registry_task.configure(queueing_lock=str(supplier.id)).defer(
            **task_kwargs
        )
