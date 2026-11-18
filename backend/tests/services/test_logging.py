"""Structured logging pipeline: JSON rendering, correlation, redaction.

Render tests reconfigure logging with LOG_JSON=True and restore the
development configuration afterwards, reading the stdout lines the handlers
write through capsys.
"""

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import tasks
from app.core.config import settings
from app.core.logging import configure_logging, redact_sensitive

HEALTH_PATH = "/api/v1/utils/health-check/"


@pytest.fixture
def json_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "LOG_JSON", True)
    configure_logging()
    yield
    monkeypatch.undo()
    configure_logging()


def _payloads(capsys: pytest.CaptureFixture[str], event: str) -> list[dict[str, Any]]:
    found = []
    for line in capsys.readouterr().out.splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if payload.get("event") == event:
            found.append(payload)
    return found


def test_redact_sensitive_masks_known_keys() -> None:
    event_dict = {"password": "hunter2", "email": "a@b.c", "invoice_id": "x"}
    masked = redact_sensitive(None, "info", event_dict)
    assert masked["password"] == "***"
    assert masked["email"] == "***"
    assert masked["invoice_id"] == "x"


def test_request_log_line_carries_correlation_id(
    client: TestClient, json_logs: None, capsys: pytest.CaptureFixture[str]
) -> None:
    response = client.get("/api/v1/invoices")  # 401 - unauthenticated
    lines = _payloads(capsys, "request")

    assert len(lines) == 1
    request_line = lines[0]
    assert request_line["status"] == 401
    assert request_line["method"] == "GET"
    assert request_line["path"] == "/api/v1/invoices"
    assert isinstance(request_line["duration_ms"], (int, float))
    # the middleware generated a UUID and echoed it to the client
    assert request_line["correlation_id"] == response.headers["x-request-id"]
    uuid.UUID(response.headers["x-request-id"])


def test_authenticated_request_logs_user_id(
    client: TestClient,
    json_logs: None,
    capsys: pytest.CaptureFixture[str],
    superuser_token_headers: dict[str, str],
) -> None:
    response = client.get("/api/v1/users/me", headers=superuser_token_headers)
    assert response.status_code == 200
    lines = _payloads(capsys, "request")
    assert lines and lines[0].get("user_id")


def test_probe_path_is_not_logged(
    client: TestClient, json_logs: None, capsys: pytest.CaptureFixture[str]
) -> None:
    client.get(HEALTH_PATH)
    assert _payloads(capsys, "request") == []


def test_foreign_records_are_redacted(
    json_logs: None, capsys: pytest.CaptureFixture[str]
) -> None:
    logging.getLogger("foreign.test").info(
        "invitation prepared", extra={"email": "someone@example.org"}
    )
    lines = _payloads(capsys, "invitation prepared")
    assert lines and lines[0]["email"] == "***"
    assert "someone@example.org" not in json.dumps(lines)


def test_parse_task_carries_request_id_across_the_queue(
    json_logs: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LOG_LEVEL", "DEBUG")
    configure_logging()
    tasks.parse_invoice_task(invoice_id=str(uuid.uuid4()), request_id="corr-42")
    lines = _payloads(capsys, "parse_invoice task started")
    assert lines
    assert lines[0]["correlation_id"] == "corr-42"
    assert lines[0]["invoice_id"]
