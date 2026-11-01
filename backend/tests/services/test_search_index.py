"""Meilisearch mirror: documents pushed, ranked ids and the graceful fallback."""

import uuid
from typing import Any

import pytest

from app.core.config import settings
from app.services import search_index
from tests.utils.invoice import create_invoice


class FakeResponse:
    def __init__(self, payload: Any = None) -> None:
        self._payload = payload if payload is not None else {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class Recorder:
    """Stands in for `_request`; returning None simulates an unreachable server."""

    def __init__(self, canned: dict[str, Any] | None = None, *, down: bool = False):
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.canned = canned or {}
        self.down = down

    def __call__(self, method: str, path: str, **kw: Any) -> FakeResponse | None:
        self.calls.append((method, path, kw))
        if self.down:
            return None
        return FakeResponse(self.canned.get(path, {}))


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "MEILISEARCH_ENABLED", True)
    monkeypatch.setattr(search_index, "_index_ready", False)


def test_sync_uploads_document(db, monkeypatch, enabled) -> None:
    invoice = create_invoice(db)
    recorder = Recorder()
    monkeypatch.setattr(search_index, "_request", recorder)
    search_index.sync(db, invoice)
    posts = [
        c for c in recorder.calls if c[0] == "POST" and c[1].endswith("/documents")
    ]
    assert posts, recorder.calls
    doc = posts[0][2]["json"][0]
    assert doc["id"] == str(invoice.id)
    assert doc["invoice_number"] == "2024000123"


def test_sync_is_noop_when_disabled(db, monkeypatch) -> None:
    invoice = create_invoice(db)
    recorder = Recorder()
    monkeypatch.setattr(search_index, "_request", recorder)
    search_index.sync(db, invoice)
    assert recorder.calls == []


def test_search_ids_parses_hits(monkeypatch, enabled) -> None:
    hit_id = uuid.uuid4()
    recorder = Recorder(
        {
            "/indexes/invoices/search": {
                "hits": [{"id": str(hit_id)}],
                "estimatedTotalHits": 42,
            }
        }
    )
    monkeypatch.setattr(search_index, "_request", recorder)
    ids, total = search_index.search_ids(
        "acme", state="needs_review", limit=20, offset=0
    )
    assert ids == [hit_id]
    assert total == 42
    search_call = recorder.calls[-1]
    assert search_call[2]["json"]["filter"] == "state = 'needs_review'"


def test_search_ids_returns_none_when_down(monkeypatch, enabled) -> None:
    monkeypatch.setattr(search_index, "_request", Recorder(down=True))
    assert search_index.search_ids("acme", limit=20, offset=0) is None
