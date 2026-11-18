"""Task queue plumbing: transient LLM failures, transactional defer, inline mode.

The suite runs with QUEUE_MODE=inline (conftest), so only the module-local
queue_schema fixture talks to real procrastinate tables.
"""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from procrastinate import schema as procrastinate_schema
from sqlmodel import Session

from app import tasks
from app.core.config import settings
from app.core.db import engine
from app.models import Invoice, InvoiceState, get_datetime_utc
from app.services import invoices as invoice_service
from app.services import parsing
from tests.utils.invoice import SAMPLE_PDF_TEXT, create_invoice

# same statements as the alembic migration a7f3c2d84e91
UNINSTALL_SQL = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename LIKE 'procrastinate%'
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.tablename);
    END LOOP;
    FOR r IN
        SELECT typname FROM pg_type
        WHERE typname LIKE 'procrastinate%'
    LOOP
        EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', r.typname);
    END LOOP;
    FOR r IN
        SELECT oid::regprocedure AS sig FROM pg_proc
        WHERE proname LIKE 'procrastinate%'
    LOOP
        EXECUTE format('DROP FUNCTION %s CASCADE', r.sig);
    END LOOP;
END $$;
"""


@pytest.fixture(scope="module")
def queue_schema(db) -> None:  # noqa: ARG001 - session-wide tables first
    sql_dir = Path(procrastinate_schema.migrations_path).parent
    # raw psycopg (no params): the scripts contain literal % and run the
    # simple protocol, so the whole multi-statement file goes in one round-trip
    with psycopg.connect(tasks._DSN, autocommit=True) as conn:
        conn.execute((sql_dir / "schema.sql").read_text())
    yield
    with psycopg.connect(tasks._DSN, autocommit=True) as conn:
        conn.execute(UNINSTALL_SQL)


def _job_count(invoice_id: object) -> int:
    """Count queue jobs from a separate connection (committed state only)."""
    with psycopg.connect(tasks._DSN) as conn:
        row = conn.execute(
            'SELECT count(*) FROM procrastinate_jobs WHERE "lock" = %s',
            (str(invoice_id),),
        ).fetchone()
    return int(row[0])


def _raise_connect(*_args: object, **_kwargs: object) -> str:
    raise httpx.ConnectError("LLM is down")


def _raise_transient(*_args: object, **_kwargs: object) -> str:
    raise parsing.TransientLLMError("LLM is down")


# --- transient vs permanent LLM failures --------------------------------------


def test_transport_failure_raises_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsing, "call_llm", _raise_connect)
    with pytest.raises(parsing.TransientLLMError):
        parsing.parse_invoice_text("\n".join(SAMPLE_PDF_TEXT), image=b"\x00\xff")


def test_http_4xx_is_not_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_auth(*_args: object, **_kwargs: object) -> str:
        request = httpx.Request("POST", "http://llm.local")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("nope", request=request, response=response)

    monkeypatch.setattr(parsing, "call_llm", raise_auth)
    # permanent errors keep the rules-only fallback - the queue must not retry
    parsing.parse_invoice_text("\n".join(SAMPLE_PDF_TEXT), image=b"\x00\xff")


def test_process_invoice_keeps_parsing_on_transient(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice = create_invoice(db, InvoiceState.UPLOADED)
    monkeypatch.setattr(
        invoice_service.storage,
        "get_storage",
        lambda: SimpleNamespace(open=lambda *_: b"x"),
    )
    monkeypatch.setattr(parsing, "extract_text", lambda *_args: "text")
    monkeypatch.setattr(parsing, "parse_invoice_text", _raise_transient)

    with pytest.raises(parsing.TransientLLMError):
        invoice_service.process_invoice(invoice.id)

    db.expire_all()
    assert db.get(Invoice, invoice.id).state == InvoiceState.PARSING.value


def test_run_parse_inline_surfaces_transient_failure(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice = create_invoice(db, InvoiceState.PARSING)
    monkeypatch.setattr(
        invoice_service.storage,
        "get_storage",
        lambda: SimpleNamespace(open=lambda *_: b"x"),
    )
    monkeypatch.setattr(parsing, "extract_text", lambda *_args: "text")
    monkeypatch.setattr(parsing, "parse_invoice_text", _raise_transient)

    tasks.run_parse_inline(invoice.id)  # must not raise

    db.expire_all()
    fresh = db.get(Invoice, invoice.id)
    assert fresh.state == InvoiceState.NEEDS_REVIEW.value
    assert fresh.parse_error and "LLM" in fresh.parse_error


# --- emails --------------------------------------------------------------------


def test_enqueue_email_inline_calls_send_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr("app.utils.send_email", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.local")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "no-reply@example.org")

    tasks.enqueue_email(email_to="a@b.c", subject="s", html_content="<p>hi</p>")

    assert calls == [
        {
            "email_to": "a@b.c",
            "subject": "s",
            "html_content": "<p>hi</p>",
        }
    ]


def test_enqueue_email_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr("app.utils.send_email", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(settings, "SMTP_HOST", None)

    tasks.enqueue_email(email_to="a@b.c", subject="s", html_content="<p>hi</p>")

    assert calls == []


# --- queue mode (transactional outbox) -----------------------------------------


def test_defer_participates_in_the_invoice_transaction(
    db,
    queue_schema,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    monkeypatch.setattr(settings, "QUEUE_MODE", "queue")
    invoice = create_invoice(db, InvoiceState.UPLOADED)
    assert _job_count(invoice.id) == 0

    with Session(engine) as session, session.begin():
        tasks.enqueue_invoice_parse(session, invoice.id)
        # the job is invisible until the whole transaction commits
        assert _job_count(invoice.id) == 0

    assert _job_count(invoice.id) == 1

    # a second defer for the same invoice is swallowed by the queueing lock
    tasks.enqueue_invoice_parse_detached(invoice.id)
    assert _job_count(invoice.id) == 1

    with Session(engine) as rollback_session:
        tasks.enqueue_invoice_parse(rollback_session, invoice.id)
        rollback_session.rollback()
    assert _job_count(invoice.id) == 1


def test_rescue_job_surfaces_stale_invoice(
    db,
    queue_schema,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    monkeypatch.setattr(settings, "QUEUE_MODE", "queue")
    invoice = create_invoice(db, InvoiceState.PARSING)
    invoice.updated_at = get_datetime_utc() - timedelta(days=1)
    db.add(invoice)
    db.commit()

    tasks._ensure_open()
    tasks.rescue_stalled_parsing_job(0)

    db.expire_all()
    assert db.get(Invoice, invoice.id).state == InvoiceState.NEEDS_REVIEW.value
