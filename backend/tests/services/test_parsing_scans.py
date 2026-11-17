"""Scanned PDFs must reach the vision model even when no OCR engine exists."""

import base64
import shutil
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.core.config import settings
from app.models import InvoiceState
from app.services import parsing
from tests.utils.invoice import SAMPLE_PDF_TEXT, make_pdf_bytes, parsed_sample

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ICO = "47123737"


@pytest.fixture
def storage_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", tmp_path)
    return tmp_path


@pytest.fixture
def poppler() -> bool:
    return shutil.which("pdftoppm") is not None


def test_render_pdf_first_page_produces_image(poppler: bool) -> None:
    if not poppler:
        pytest.skip("pdftoppm (poppler) is not installed")
    page = parsing.render_pdf_first_page(make_pdf_bytes(SAMPLE_PDF_TEXT))
    assert page is not None
    assert page[:2] == b"\xff\xd8"  # JPEG SOI marker


def test_render_pdf_first_page_without_poppler(monkeypatch) -> None:
    monkeypatch.setattr(parsing.shutil, "which", lambda _name: None)
    assert parsing.render_pdf_first_page(b"%PDF-1.4 junk") is None


def capture_post(monkeypatch, response_content: str) -> dict[str, Any]:
    calls: dict[str, Any] = {"requests": []}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": response_content}}]}

    def fake_post(_url: str, **kwargs: Any) -> FakeResponse:
        calls["requests"].append(kwargs)
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://llm.test:1234/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "key")
    return calls


def image_url_of(calls: dict[str, Any]) -> str:
    request = calls["requests"][0]["json"]
    return request["messages"][1]["content"][1]["image_url"]["url"]


def test_call_llm_uses_png_data_url_for_png(monkeypatch) -> None:
    calls = capture_post(monkeypatch, "{}")
    parsing.call_llm("prompt", image=PNG_BYTES)
    url = image_url_of(calls)
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",")[1].encode()) == PNG_BYTES


def test_call_llm_keeps_jpeg_data_url(monkeypatch) -> None:
    calls = capture_post(monkeypatch, "{}")
    parsing.call_llm("prompt", image=b"\xff\xd8\xff" + b"jpeg")
    assert image_url_of(calls).startswith("data:image/jpeg;base64,")


def upload_pdf(client, headers) -> dict:
    # unique content - the same bytes twice would be rejected as a duplicate
    pdf = make_pdf_bytes(SAMPLE_PDF_TEXT + [f"poznamka-{uuid4().hex}"])
    response = client.post(
        "/api/v1/invoices/upload",
        headers=headers,
        files={"file": ("sken.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 201
    detail = client.get(f"/api/v1/invoices/{response.json()['id']}", headers=headers)
    assert detail.status_code == 200
    return detail.json()


def test_scanned_pdf_without_text_goes_to_vision(
    client, superuser_token_headers, storage_dir, monkeypatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_parse(text: str, image: bytes | None = None):
        seen["text"] = text
        seen["image"] = image
        return parsed_sample(ico=ICO)

    monkeypatch.setattr(parsing, "extract_text", lambda _d, _ct: "")
    monkeypatch.setattr(parsing, "render_pdf_first_page", lambda _data: PNG_BYTES)
    monkeypatch.setattr(parsing, "parse_invoice_text", fake_parse)

    invoice = upload_pdf(client, superuser_token_headers)

    assert seen["text"] == ""
    assert seen["image"] == PNG_BYTES
    assert invoice["invoice_number"] == "2024000123"


def test_scanned_pdf_without_renderer_reports_parse_error(
    client, superuser_token_headers, storage_dir, monkeypatch
) -> None:
    monkeypatch.setattr(parsing, "extract_text", lambda _d, _ct: "")
    monkeypatch.setattr(parsing, "render_pdf_first_page", lambda _data: None)

    invoice = upload_pdf(client, superuser_token_headers)

    assert invoice["state"] == InvoiceState.NEEDS_REVIEW.value
    assert invoice["parse_error"]
