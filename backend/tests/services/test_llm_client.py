"""Unit tests for the OpenAI-compatible LLM client in app.services.parsing."""

from typing import Any

import httpx

from app.core.config import settings
from app.services import parsing


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def completion(content: str) -> FakeResponse:
    return FakeResponse(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


def capture_post(monkeypatch, responses: list[FakeResponse]) -> dict[str, Any]:
    calls: dict[str, Any] = {"count": 0, "requests": []}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        calls["count"] += 1
        calls["requests"].append({"url": url, **kwargs})
        return responses[min(calls["count"] - 1, len(responses) - 1)]

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_call_llm_request_shape(monkeypatch):
    calls = capture_post(monkeypatch, [completion("{}")])
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://llm.test:1234/v1/")
    monkeypatch.setattr(settings, "LLM_API_KEY", "secret-key")

    content = parsing.call_llm("Text dokladu: …")

    assert content == "{}"
    request = calls["requests"][0]
    assert request["url"] == "http://llm.test:1234/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer secret-key"
    payload = request["json"]
    assert payload["model"] == settings.LLM_MODEL
    assert payload["stream"] is False
    assert payload["temperature"] == 0.1
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "parsed_invoice"
    # the schema is also embedded in the system prompt, so servers that
    # ignore response_format still get the expected shape
    system = payload["messages"][0]
    assert system["role"] == "system"
    assert "properties" in system["content"]
    assert payload["messages"][1] == {"role": "user", "content": "Text dokladu: …"}


def test_call_llm_without_api_key_sends_no_auth_header(monkeypatch):
    calls = capture_post(monkeypatch, [completion("{}")])
    monkeypatch.setattr(settings, "LLM_API_KEY", None)

    parsing.call_llm("text")

    assert "Authorization" not in calls["requests"][0]["headers"]


def test_call_llm_vision_payload(monkeypatch):
    calls = capture_post(monkeypatch, [completion("{}")])
    monkeypatch.setattr(settings, "LLM_VISION_MODEL", "vision-model")

    parsing.call_llm("Rozpoznej data", image=b"fakejpeg")

    payload = calls["requests"][0]["json"]
    assert payload["model"] == "vision-model"
    message = payload["messages"][1]
    assert message["content"][0]["type"] == "text"
    data_url = message["content"][1]["image_url"]["url"]
    assert data_url.startswith("data:image/jpeg;base64,")


def test_call_llm_vision_falls_back_to_main_model(monkeypatch):
    calls = capture_post(monkeypatch, [completion("{}")])
    monkeypatch.setattr(settings, "LLM_VISION_MODEL", None)

    parsing.call_llm("Rozpoznej data", image=b"fakejpeg")

    assert calls["requests"][0]["json"]["model"] == settings.LLM_MODEL


def test_parse_invoice_text_validates_completion(monkeypatch):
    calls = capture_post(
        monkeypatch,
        [completion('{"invoice_number": "2024000123", "total_price": "1210,50"}')],
    )

    parsed = parsing.parse_invoice_text("Text dokladu")

    assert parsed is not None
    assert parsed.invoice_number == "2024000123"
    assert str(parsed.total_price) == "1210.50"  # Czech comma coerced
    assert calls["count"] == 1


def test_parse_invoice_text_retries_once_on_malformed_json(monkeypatch):
    calls = capture_post(
        monkeypatch,
        [
            completion("tohoto rozhodne JSON neni"),
            completion('{"invoice_number": "X"}'),
        ],
    )

    parsed = parsing.parse_invoice_text("Text dokladu")

    assert parsed is not None
    assert parsed.invoice_number == "X"
    assert calls["count"] == 2


def test_parse_invoice_text_gives_up_after_two_attempts(monkeypatch):
    calls = capture_post(monkeypatch, [completion("text, ne JSON")])

    assert parsing.parse_invoice_text("Text dokladu") is None
    assert calls["count"] == 2


def test_parse_invoice_text_returns_none_on_http_error(monkeypatch):
    def fail(*args: Any, **kwargs: Any) -> FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fail)

    assert parsing.parse_invoice_text("Text dokladu") is None


def test_parse_invoice_text_empty_input_skips_call(monkeypatch):
    calls = capture_post(monkeypatch, [completion("{}")])

    assert parsing.parse_invoice_text("   ") is None
    assert calls["count"] == 0


def test_llm_schema_has_no_lookaround_patterns():
    # vLLM/xgrammar rejects regexes with lookaround (pydantic emits them for
    # Decimal fields), the sent schema must be grammar-safe
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            pattern = node.get("pattern")
            assert not (isinstance(pattern, str) and "(?" in pattern)
            return all(walk(value) for value in node.values())
        if isinstance(node, list):
            return all(walk(value) for value in node)
        return True

    walk(parsing._llm_schema())
