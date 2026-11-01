"""Invoice data extraction.

Pipeline: stored file -> text (pypdf / tesseract OCR) -> LLM structured
extraction (OpenAI-compatible API) -> field normalization + VAT check.

All external calls (`extract_text`, `parse_invoice_text`) are module-level
functions so tests can monkeypatch them without touching the pipeline.
"""

import base64
import difflib
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import httpx
from pypdf import PdfReader
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (  # noqa: F401 (re-export)
    Company,
    ParsedInvoice,
    VatItem,
    normalize_bank_account,
    normalize_iban,
)

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


logger = logging.getLogger(__name__)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _ocr_image(path: Path) -> str:
    tesseract = settings.TESSERACT_PATH and shutil.which(settings.TESSERACT_PATH)
    if not tesseract:
        return ""
    result = _run([tesseract, str(path), "stdout", "-l", "ces+eng", "--psm", "3"])
    return result.stdout if result.returncode == 0 else ""


def _ocr_pdf(data: bytes) -> str:
    """OCR a scanned PDF: pdftoppm (first pages) + tesseract, if available."""
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm or not (
        settings.TESSERACT_PATH and shutil.which(settings.TESSERACT_PATH)
    ):
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf_path = tmp_path / "input.pdf"
        pdf_path.write_bytes(data)
        conversion = _run(
            [
                pdftoppm,
                "-png",
                "-r",
                "200",
                "-firstpage",
                "1",
                "-lastpage",
                "5",
                str(pdf_path),
                str(tmp_path / "page"),
            ]
        )
        if conversion.returncode != 0:
            return ""
        chunks = []
        for page in sorted(tmp_path.glob("page*.png")):
            chunks.append(_ocr_image(page))
        return "\n".join(chunks)


def extract_text(data: bytes, content_type: str) -> str:
    """Best-effort plain text of an uploaded document (PDF text or OCR)."""
    if content_type == "application/pdf":
        text = ""
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            text = ""
        if len(text.strip()) < 40:
            text = _ocr_pdf(data)
        return text
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as tmp:
        tmp.write(data)
        image_path = Path(tmp.name)
    try:
        return _ocr_image(image_path)
    finally:
        image_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# LLM structured extraction via an OpenAI-compatible chat completions API
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "Jsi účetní asistent pro české účetní doklady. Z textu faktury nebo "
    "jiného dokladu vytáhni strukturovaná data. Datumy ve formátu ISO "
    "(RRRR-MM-DD) nebo česky D.M.RRRR. Částky jako číslo s tečkou. IČO je 8 "
    "číslic bez mezer, DIČ ve formátu CZ plus číslice. supplier_bank_account "
    "uveď ve formátu číslo/bank (např. 123456789/0100), supplier_iban jako "
    "související IBAN příjemce (např. CZ88030000000000912923). Má-li doklad "
    "účtů více (např. korunový a cizoměnový), uveď všechny do "
    "supplier_bank_accounts jako seznam objektů {account, iban}, hlavní "
    "korunový účet jako první. vat_items: jeden "
    "řádek pro každou sazbu DPH – base_price = základ daně, tax_price = "
    "DPH, total_price = celkem včetně DPH. Na dokladu bývá více sazeb "
    "(např. 15 % a 21 %, DE 19 % a 7 %, SK 20 %/10 %, AT 20 %/10 %/13 %) – "
    "uveď samostatný řádek pro každou, i když jde o starší nebo cizí sazbu. "
    "Je-li část řádků v jiné měně než faktura (např. informační přepočet), "
    "vypň u nich pole currency; 0% plnění (MPO, reverse charge) má "
    'vat_rate "0%" a tax_price 0. Pokud pole nemůžeš spolehlivě '
    "určit, nech ho null. Žádná data nevymýšlej."
)


def _llm_schema() -> dict[str, Any]:
    schema = ParsedInvoice.model_json_schema()
    defs = schema.pop("$defs", None) or {}
    _inline_refs(schema, defs)
    _strip_lookaround_patterns(schema)
    return schema


def _inline_refs(node: Any, defs: dict[str, Any], depth: int = 0) -> None:
    """Replace `$ref`s with their definitions in-place.

    Guided-decoding engines (xgrammar in vLLM) compile inlined schemas far
    faster than ones with $defs/$ref indirection.
    """
    if depth > 8 or not isinstance(node, (dict, list)):
        return
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                node.clear()
                node.update(target)
        for value in node.values():
            _inline_refs(value, defs, depth + 1)
    else:
        for value in node:
            _inline_refs(value, defs, depth + 1)


def _strip_lookaround_patterns(node: Any) -> None:
    """Drop regex patterns with lookaround in-place.

    Pydantic emits them for Decimal fields, but the grammar compilers behind
    OpenAI-compatible structured-output implementations (xgrammar in vLLM,
    …) cannot compile lookaround. Removing them only relaxes the constraint –
    our Pydantic validators coerce and check the values afterwards anyway.
    """
    if isinstance(node, dict):
        pattern = node.get("pattern")
        if isinstance(pattern, str) and "(?" in pattern:
            del node["pattern"]
        for value in node.values():
            _strip_lookaround_patterns(value)
    elif isinstance(node, list):
        for value in node:
            _strip_lookaround_patterns(value)


def _system_prompt() -> str:
    # Servers that ignore `response_format` still get the expected shape.
    schema = json.dumps(_llm_schema(), ensure_ascii=False)
    return f"{_SYSTEM_PROMPT}\nOdpovědej POUZE validním JSON objektem:\n{schema}"


def call_llm(
    prompt: str,
    image: bytes | None = None,
    *,
    structured: bool = True,
    timeout: int | None = None,
) -> str:
    """Single chat completion against the configured OpenAI-compatible API."""
    if image is None:
        model = settings.LLM_MODEL
        content: Any = prompt
    else:
        model = settings.LLM_VISION_MODEL or settings.LLM_MODEL
        data_url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": content},
        ],
        "stream": False,
        "temperature": 0.1,
    }
    if structured:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "parsed_invoice",
                "schema": _llm_schema(),
                "strict": False,
            },
        }
    response = httpx.post(
        f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
        json=payload,
        headers=headers,
        timeout=timeout or settings.LLM_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return str(response.json()["choices"][0]["message"]["content"])


def parse_invoice_text(text: str, image: bytes | None = None) -> ParsedInvoice | None:
    """Extract invoice fields: PDF-text rules first, LLM for what they miss."""
    if not text.strip() and image is None:
        return None
    # text-layer documents are parsed with rules first; the LLM is only asked
    # when the rules cannot cover the essential fields (or a scanned image)
    rules = rule_extract(text) if text.strip() else None
    if rules is not None and image is None:
        logger.info("extracted from the PDF text layer without the LLM")
        return rules
    prompt = (
        f"Text dokladu:\n\n{text[:15000]}"
        if text
        else "Rozpoznej data z obrazu dokladu."
    )
    # Guided decoding is slower on long documents (the grammar walks every
    # token), while plain JSON prompting degrades gracefully – try each first
    # depending on how much text the document carries.
    order = [True, False] if len(text) <= 4000 else [False, True]
    for attempt, structured in enumerate(order):
        try:
            content = call_llm(
                prompt,
                image=image,
                structured=structured,
                timeout=settings.LLM_STRUCTURED_TIMEOUT_SECONDS if structured else None,
            )
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning(
                "LLM attempt %d (%s) failed: %s",
                attempt + 1,
                "guided" if structured else "plain",
                exc,
            )
            continue
        try:
            parsed = ParsedInvoice.model_validate(json.loads(content))
        except json.JSONDecodeError, ValueError:
            logger.warning(
                "LLM attempt %d (%s) returned unparsable content",
                attempt + 1,
                "guided" if structured else "plain",
            )
            continue
        if rules is not None:
            parsed = _merge_missing(parsed, rules)
        return parsed
    # the LLM is unreachable or kept failing – return whatever the rules found
    return rules


_MERGE_FIELDS = (
    "invoice_number",
    "supplier_name",
    "supplier_ico",
    "supplier_dic",
    "supplier_bank_account",
    "supplier_iban",
    "supplier_bank_accounts",
    "document_type",
    "issue_date",
    "delivery_date",
    "due_date",
    "total_price",
    "total_vat",
    "total_without_vat",
    "currency",
    "vat_items",
)


def _merge_missing(parsed: ParsedInvoice, extra: ParsedInvoice) -> ParsedInvoice:
    """Fill fields the LLM left empty with values found by the rules."""
    for name in _MERGE_FIELDS:
        if getattr(parsed, name, None) in (None, ""):
            value = getattr(extra, name, None)
            if value:
                setattr(parsed, name, value)
    return parsed


# ---------------------------------------------------------------------------
# Rule-based extraction from the PDF text layer
# ---------------------------------------------------------------------------

_CZ_DATE_RE = re.compile(
    r"\b(\d{1,2})[.\s-]+(\d{1,2})[.\s-]+(\d{2,4})\b|\b(\d{4})-(\d{2})-(\d{2})\b"
)
_INVOICE_NUMBER_RES = (
    re.compile(
        r"faktur(?:y|a)\s*(?:č\.?|číslo|number|no\.?|#)?\s*[:.]?\s*([A-Z0-9][A-Z0-9/\-]{3,29})",
        re.IGNORECASE,
    ),
    re.compile(
        r"daňový\s+doklad\s*(?:č\.?|číslo)?\s*[:.]?\s*([A-Z0-9][A-Z0-9/\-]{3,29})",
        re.IGNORECASE,
    ),
    re.compile(
        r"invoice\s*(?:no\.?|number|#)?\s*[:.]?\s*([A-Z0-9][A-Z0-9/\-]{3,29})",
        re.IGNORECASE,
    ),
    re.compile(r"číslo\s+dokladu\s*[:.]?\s*([A-Z0-9][A-Z0-9/\-]{3,29})", re.IGNORECASE),
    re.compile(r"var\.?\s*symbol\s*[:.]?\s*(\d{6,20})", re.IGNORECASE),
)
_ICO_RE = re.compile(
    r"(?<![a-záčďéěíňóŕšťúýž])i\.?\s?[cč](?:\.?\s?o)?\.?\s*[:.]?\s*(\d{8})(?!\d)",
    re.IGNORECASE,
)
_DIC_RE = re.compile(
    r"dič\s*/?\s*(?:vat)?\s*[:.]?\s*(CZ\s?\d{8,10}|\d{8,10})",
    re.IGNORECASE,
)
_BARE_DIC_RE = re.compile(r"\b(CZ\d{10})\b")
_BANK_ROW_RE = re.compile(r"(?<![\d.,/-])((?:\d{4}-)?\d{5,20}\s*/\s*\d{4})(?![\d.])")
_IBAN_RE = re.compile(r"\b(CZ\d{2}(?:\s?\d{4}){5}|CZ\d{22})\b")
_TOTAL_RES = (
    re.compile(
        r"(?:cena\s+celkem|celkem(?:\s+k\s+úhradě)?|sumace|celková\s+částka|total)"
        r"(?P<label>[^\d\n]{0,30}?)\s*(?P<amount>\d{1,3}(?: \d{3})+(?:[.,]\d{1,2})?"
        r"|\d+(?:[.,]\d{1,2})?)\s*(?P<cur>Kč|CZK|EUR|€)?",
        re.IGNORECASE,
    ),
)
_DPH_RE = re.compile(r"\bDPH[:.]?\s+(\d{1,3}(?:[ .]\d{3})*(?:[.,]\d{1,3})?)")
_BUYER_LABEL_RE = re.compile(
    r"(?:odběratel|prijemce|odberatel|zákazník|zakaznik|customer|objednávající)",
    re.IGNORECASE,
)
_SELLER_LABEL_RE = re.compile(
    r"(?:dodavatel|prodávající|supplier|exporter|\bfaktura\b|daňový doklad)",
    re.IGNORECASE,
)
_CORP_LINE_RE = re.compile(
    r"^[ \t]*([^\n]{2,120}?\b(?:s\.?\s?r\.?\s?o\.?|a\.\s?s\.?|v\.?o\.?k\.?"
    r"|k\.\s?s\.?|se)(?![a-z]))",
    re.IGNORECASE | re.MULTILINE,
)
_CZ_MONTHS = {
    "ledna": 1,
    "února": 2,
    "března": 3,
    "dubna": 4,
    "května": 5,
    "června": 6,
    "července": 7,
    "srpna": 8,
    "září": 9,
    "října": 10,
    "listopadu": 11,
    "prosince": 12,
}


def _parse_cz_date(raw: str) -> date | None:
    named = re.search(
        r"(\d{1,2})\.?\s+(ledna|února|března|dubna|května|června|července|srpna|"
        r"září|října|listopadu|prosin[cz])\s+(\d{4})",
        raw,
        re.IGNORECASE,
    )
    if named:
        name = named.group(2).lower()
        month = _CZ_MONTHS.get(name) or (12 if name.startswith("prosin") else 0)
        try:
            return (
                date(int(named.group(3)), month, int(named.group(1))) if month else None
            )
        except ValueError:
            return None
    match = _CZ_DATE_RE.search(raw)
    if not match:
        return None
    if match.group(4):
        parts: tuple[int, int, int] = (
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
        )
    else:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000 if year < 70 else 1900
        parts = (year, month, day)
    try:
        return date(*parts)
    except ValueError:
        return None


def _find_labeled_date(text: str, labels: str) -> date | None:
    match = re.search(
        rf"(?:{labels})[^0-9]{{0,25}}(\d{{1,2}}[.\s-]+\d{{1,2}}[.\s-]+\d{{2,4}}"
        rf"|\d{{4}}-\d{{2}}-\d{{2}}"
        rf"|\d{{1,2}}\.\s*[a-zá-ž]+\.?\s+\d{{4}})",
        text,
        re.IGNORECASE,
    )
    return _parse_cz_date(match.group(1)) if match else None


def _amount_value(raw: str) -> float:
    cleaned = re.sub(r"[^\d.,]", "", raw)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned) if cleaned else 0.0


def _supplier_identity(text: str) -> tuple[str | None, str | None, str | None]:
    """Return (name, ico, dic) of the seller, skipping buyer-block matches.

    Czech invoices list both parties; the seller is the block whose company
    identification sits closest below a line with a legal-form suffix.
    """
    dic_matches = list(_DIC_RE.finditer(text)) or list(_BARE_DIC_RE.finditer(text))
    matches: list[tuple[Any, int, int, str]] = [
        (m, 0, m.start(), m.group(1)) for m in _ICO_RE.finditer(text)
    ]
    matches += [
        (m, 1, m.start(), m.group(1).replace(" ", "").upper()) for m in dic_matches
    ]
    candidates: list[
        tuple[tuple[int, int, int], str | None, str | None, str | None]
    ] = []
    for _match, kind, start, value in matches:
        window = text[max(0, start - 320) : start]
        buyer = list(_BUYER_LABEL_RE.finditer(window))
        seller = list(_SELLER_LABEL_RE.finditer(window))
        if buyer and (not seller or buyer[-1].start() > seller[-1].start()):
            continue  # this identification number belongs to the buyer
        corp_lines = list(_CORP_LINE_RE.finditer(window))
        name = None
        if corp_lines:
            name = (
                re.sub(
                    r"^(?:společnost|dodavatel)\s*[:.]?\s*",
                    "",
                    corp_lines[-1].group(1),
                    flags=re.IGNORECASE,
                ).strip(" ,:")[:200]
                or None
            )
        rank = (0 if name else 1, kind, start)
        candidates.append(
            (rank, name, value if kind == 0 else None, value if kind == 1 else None)
        )
    if not candidates:
        return None, None, None
    best = min(candidates, key=lambda item: item[0])
    name, ico, dic = best[1], best[2], best[3]
    # combine with the other identification kind when both point to the seller
    if dic is None:
        dic_candidates = [c for c in candidates if c[3]]
        if dic_candidates:
            dic = min(dic_candidates, key=lambda item: item[0])[3]
    if ico is None:
        ico_candidates = [c for c in candidates if c[2]]
        if ico_candidates:
            ico = min(ico_candidates, key=lambda item: item[0])[2]
    return name, ico, dic


def _find_vat_total(text: str) -> str | None:
    """The "DPH" summary amount, skipping rate ("21,0 %") and tax-base mentions."""
    for match in _DPH_RE.finditer(text):
        raw = match.group(1)
        after = text[match.end() : match.end() + 4]
        before = text[max(0, match.start() - 12) : match.start()]
        decimals = re.split(r"[.,]", raw)[-1]
        if "%" in after or (decimals != raw and len(decimals) >= 3):
            continue  # a rate like "21,000 %", not an amount
        if re.search(r"(?:základ|bez)\s*$", before.split("\n")[-1], re.IGNORECASE):
            continue  # "Základ DPH"/"Celkem bez DPH" mention amounts, not the VAT
        return raw
    return None


# ---------------------------------------------------------------------------
# VAT rate lines (rule based)
# ---------------------------------------------------------------------------


def _money(name: str) -> str:
    """Regex fragment: one amount with an optional trailing currency token."""
    return (
        rf"(?P<{name}>\d{{1,3}}(?:[ .]\d{{3}})*(?:[.,]\d{{1,3}})?|\d+(?:[.,]\d{{1,2}})?)"
        rf"(?:\s*(?P<{name}_cur>CZK|K[čc]|EUR|€))?"
    )


_VAT_RATE = r"(?P<rate>\d{1,3}(?:[.,]\d{1,3})?)\s*%"
_VAT_ROW_PATTERNS = (
    # "Sazba DPH 21 %  Základ DPH 1 500,00 Kč  DPH 315,00 Kč"
    re.compile(
        rf"(?:sazba\s+)?{_VAT_RATE}[^0-9\n]{{0,12}}(?:základ|zaklad)\s*(?:dan[ěe])?"
        rf"[^0-9\n]{{0,6}}{_money('base')}[^0-9\n]{{0,12}}(?:dph|da[ňn])"
        rf"[^0-9\n]{{0,6}}{_money('tax')}",
        re.IGNORECASE,
    ),
    # "Základ DPH 20 % 100,00 EUR Daň 20,00 EUR"
    re.compile(
        rf"z[aá]kl[aá]d\s+(?:dph|dan[ěe])\s+{_VAT_RATE}\s*{_money('base')}"
        rf"[^0-9\n]{{0,10}}(?:dph|da[ňn])\s*{_money('tax')}",
        re.IGNORECASE,
    ),
    # "Základ daně 21%: 1 000,00  DPH: 210,00"
    re.compile(
        rf"z[aá]kl[aá]d\s*(?:dan[ěe]\s+)?{_VAT_RATE}\s*:?\s*{_money('base')}"
        rf"\s*[,;]?\s*(?:celkem\s+)?(?:dph|da[ňn])\s*:?\s*{_money('tax')}",
        re.IGNORECASE,
    ),
    # "Základ daně 239,67 Kč  DPH 21 % 50,33 Kč"
    re.compile(
        rf"z[aá]kl[aá]d(?:\s+dan[ěe])?\s*[:.]?\s*{_money('base')}"
        rf"[^0-9\n]{{0,8}}(?:dph|da[ňn])\s+{_VAT_RATE}\s*{_money('tax')}",
        re.IGNORECASE,
    ),
    # "19 % MwSt. aus 150,00 EUR = 28,50 EUR"
    re.compile(
        rf"{_VAT_RATE}\s*(?:mwst\.?|mehrwertsteuer|umsatzsteuer|ust\.?)"
        rf"[^0-9\n]{{0,8}}(?:aus|von|from)?\s*{_money('base')}\s*(?:=|:)?\s*"
        rf"{_money('tax')}",
        re.IGNORECASE,
    ),
    # "MwSt. 19 % 28,50 EUR aus 150,00 EUR"
    re.compile(
        rf"(?:mwst\.?|mehrwertsteuer|umsatzsteuer|ust\.?)\s*{_VAT_RATE}\s*:?\s*"
        rf"{_money('tax')}[^0-9\n]{{0,8}}(?:aus|von|from)\s+{_money('base')}",
        re.IGNORECASE,
    ),
    # rate table row: "21 %   1 500,00   315,00   1 815,00"
    re.compile(
        rf"(?m)^\s*{_VAT_RATE}\s*[|]?\s*{_money('base')}\s*[|]?\s*{_money('tax')}"
        rf"(?:\s*[|]?\s*{_money('total')})?\s*[|]?\s*$",
    ),
    # item table row: "... 239,67 21% 50,33 ..." (base, rate, VAT per row)
    re.compile(rf"{_money('base')}\s+{_VAT_RATE}\s*{_money('tax')}"),
    # zero rate lines: "MPO 400,00 Kč" / "reverse charge 250,00 EUR"
    re.compile(
        rf"(?:mpo|reverse charge)[^0-9\n]{{0,14}}{_money('base')}", re.IGNORECASE
    ),
)


def _format_vat_rate(raw: str) -> str:
    value = float(raw.replace(",", "."))
    rounded = round(value)
    if abs(value - rounded) < 0.005:
        return f"{rounded}%"
    return f"{value:.1f}".replace(".", ",") + "%"


def _rate_tax_consistent(rate: float, base: Decimal, tax: Decimal) -> bool:
    """Whether tax ≈ base × rate, guarding against amounts glued to quantities."""
    if rate <= 0:
        return tax == 0
    expected = base * Decimal(str(rate)) / Decimal(100)
    return abs(expected - tax) <= max(Decimal("0.05"), tax * Decimal("0.01"))


def _row_currency(groups: dict[str, str | None]) -> str | None:
    for token in (
        groups.get("base_cur"),
        groups.get("tax_cur"),
        groups.get("total_cur"),
    ):
        if not token:
            continue
        token = token.upper()
        if token in ("CZK", "KČ", "KC"):
            return "CZK"
        if token in ("EUR", "€"):
            return "EUR"
    return None


def rule_vat_items(
    text: str, currency: str | None, total_price: float | None = None
) -> list[VatItem] | None:
    """Per-rate VAT lines from the text layer; None when inconsistent or absent.

    Handles several rates and currencies on one document; foreign-currency
    lines are kept but tagged so the totals check can filter them out.
    """
    rows: list[VatItem] = []
    claimed: list[tuple[int, int]] = []
    for pattern in _VAT_ROW_PATTERNS:
        position = 0
        while match := pattern.search(text, position):
            if any(
                start < match.end() and match.start() < end for start, end in claimed
            ):
                position = max(match.end(), position + 1)
                continue  # an earlier (more specific) pattern owns this span
            groups = match.groupdict()
            base = Decimal(f"{_amount_value(groups['base']):.2f}")
            tax_raw = groups.get("tax")
            tax = Decimal(f"{_amount_value(tax_raw):.2f}") if tax_raw else Decimal("0")
            total_raw = groups.get("total")
            row_total = (
                Decimal(f"{_amount_value(total_raw):.2f}") if total_raw else base + tax
            )
            rate = _format_vat_rate(groups["rate"]) if groups.get("rate") else "0%"
            if not _rate_tax_consistent(
                float(rate.rstrip("%").replace(",", ".")), base, tax
            ):
                position = match.start() + 1  # a digit run glued to the amount
                continue
            claimed.append((match.start(), match.end()))
            rows.append(
                VatItem(
                    vat_rate=rate,
                    base_price=base,
                    tax_price=tax,
                    total_price=row_total,
                    currency=_row_currency(groups),
                )
            )
            position = match.end()
    if not rows:
        return None
    merged: dict[tuple[str | None, str | None], VatItem] = {}
    for row in rows:
        key = (row.vat_rate, row.currency)
        if key in merged:
            acc = merged[key]
            acc.base_price += row.base_price
            acc.tax_price += row.tax_price
            acc.total_price += row.total_price
        else:
            merged[key] = row
    items = list(merged.values())
    relevant = [
        item
        for item in items
        if currency is None or item.currency is None or item.currency == currency
    ]
    if not relevant:
        return None
    if total_price is not None:
        summed = sum(float(item.base_price + item.tax_price) for item in relevant)
        if abs(summed - total_price) > 0.5:
            return None  # the lines do not reproduce the total – trust the LLM
    return items


def rule_extract(text: str) -> ParsedInvoice | None:
    """Parse fields straight from a text-layer PDF; None unless key fields fit.

    Deliberately conservative: anything doubtful is left to the LLM path.
    """
    if len(text.strip()) < 80:
        return None
    invoice_number: str | None = None
    for pattern in _INVOICE_NUMBER_RES:
        match = pattern.search(text)
        if match and any(char.isdigit() for char in match.group(1)):
            invoice_number = match.group(1).rstrip(".")
            break

    supplier_name, supplier_ico, supplier_dic = _supplier_identity(text)

    # payment account rows, each paired with the nearest IBAN around it
    ibans: list[list[Any]] = [
        [match.start(), normalize_iban(match.group(1)), False]
        for match in _IBAN_RE.finditer(text)
    ]
    rows: list[dict[str, str | None]] = []
    for match in _BANK_ROW_RE.finditer(text):
        normalized = normalize_bank_account(match.group(1))
        if not normalized or any(row["account"] == normalized for row in rows):
            continue
        iban, best = None, 1e9
        for entry in ibans:
            if entry[2] or not match.start() - 60 <= entry[0] <= match.end() + 240:
                continue
            distance = abs(entry[0] - match.end())
            if distance < best:
                iban, best = entry, distance
        if iban is not None:
            iban[2] = True
        rows.append({"account": normalized, "iban": str(iban[1]) if iban else None})
    for entry in ibans:
        if not entry[2]:
            entry[2] = True
            rows.append({"account": None, "iban": str(entry[1])})
    for row in rows:
        if row["account"] is None and row["iban"]:
            # a lone IBAN still implies the domestic account/bank pair
            row["account"] = normalize_bank_account(str(row["iban"]))

    # totals: plain "celkem" rows are candidates, "bez DPH"/"základ" are bases
    currency: str | None = None
    totals: list[tuple[str | None, float]] = []
    bases: list[float] = []
    for pattern in _TOTAL_RES:
        for match in pattern.finditer(text):
            label_part = (match.group("label") or "").lower()
            if any(word in label_part for word in ("bez", "základ", "zaklad")):
                bases.append(_amount_value(match.group("amount")))
                continue
            cur = match.group("cur")
            totals.append(
                (
                    "CZK" if cur in ("Kč", "CZK") else cur,
                    _amount_value(match.group("amount")),
                )
            )
    vat_total = _find_vat_total(text)

    body = text.upper()
    if currency is None:
        declared = re.search(r"měna\s*[:.]?\s*(CZK|EUR)", text, re.IGNORECASE)
        currency = (
            declared.group(1)
            if declared
            else (
                "CZK"
                if "KČ" in body or "CZK" in body
                else ("EUR" if "EUR" in body or "€" in body else None)
            )
        )
    same = [amount for cur, amount in totals if cur in (None, currency)]
    total_price = max(same) if same else None
    if total_price is not None and bases and abs(total_price - max(bases)) < 0.01:
        total_price = None  # only ever matched the "without VAT" row

    vat_items = rule_vat_items(text, currency, total_price)
    relevant_vat = [
        item
        for item in (vat_items or [])
        if currency is None or item.currency is None or item.currency == currency
    ]
    if vat_total is None and relevant_vat:
        vat_total = f"{sum(float(item.tax_price) for item in relevant_vat):.2f}"

    document_type: Literal["invoice", "proforma"] = "invoice"
    if re.search(r"proforma|záloha", text, re.IGNORECASE) and not re.search(
        r"\bfaktura\b|\bdaňový doklad\b", text, re.IGNORECASE
    ):
        document_type = "proforma"

    parsed = ParsedInvoice.model_validate(
        {
            "invoice_number": invoice_number,
            "supplier_name": supplier_name,
            "supplier_ico": supplier_ico,
            "supplier_dic": supplier_dic,
            "supplier_bank_accounts": rows or None,
            "document_type": document_type,
            "issue_date": _find_labeled_date(
                text,
                r"datum\s+vydán[íy]|vydáno|issued|datum(?!.{0,14}(?:splatn|dodán))",
            ),
            "delivery_date": _find_labeled_date(text, r"datum\s+dodán[íy]|dodáno"),
            "due_date": _find_labeled_date(
                text, r"datum\s+splatnosti|splatnost[:.]|due\s+date"
            ),
            "total_price": str(total_price) if total_price is not None else None,
            "total_vat": vat_total,
            "total_without_vat": str(max(bases)) if bases else None,
            "currency": currency,
            "vat_items": relevant_vat or None,
            "confidence": 0.9,
        }
    )
    known = sum(
        value is not None
        for value in (
            parsed.invoice_number,
            parsed.total_price,
            parsed.supplier_name,
            parsed.supplier_ico,
            parsed.supplier_dic,
        )
    )
    essential = (
        parsed.invoice_number
        and parsed.total_price
        and (parsed.supplier_name or parsed.supplier_ico or parsed.supplier_dic)
    )
    if not essential or known < 3:
        return None
    return normalize_parsed(parsed)


# ---------------------------------------------------------------------------
# Field normalization + company matching
# ---------------------------------------------------------------------------


def normalize_parsed(parsed: ParsedInvoice) -> ParsedInvoice:
    parsed.supplier_ico = _only_digits(parsed.supplier_ico) or None
    if parsed.supplier_dic:
        parsed.supplier_dic = parsed.supplier_dic.replace(" ", "").upper()
    if parsed.supplier_bank_account:
        parsed.supplier_bank_account = normalize_bank_account(
            parsed.supplier_bank_account
        )
    if parsed.supplier_iban:
        parsed.supplier_iban = normalize_iban(parsed.supplier_iban)
    if parsed.supplier_bank_accounts:
        # invoices (e.g. from Festo) list several payment accounts – keep the
        # main one also in the single-value fields for the Pohoda export
        if not parsed.supplier_bank_account:
            parsed.supplier_bank_account = next(
                (row.account for row in parsed.supplier_bank_accounts if row.account),
                None,
            )
        if not parsed.supplier_iban:
            parsed.supplier_iban = next(
                (row.iban for row in parsed.supplier_bank_accounts if row.iban),
                None,
            )
    return parsed


def _only_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value) if value else ""


def _dic_digits(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\D", "", value[2:] if value[:2].isalpha() else value)


def match_company(
    session: Session,
    ico: str | None,
    dic: str | None,
    name: str | None,
) -> Company | None:
    """Find the one active company matching parsed supplier data, if unambiguous."""
    companies = session.exec(select(Company).where(Company.is_active == True)).all()  # noqa: E712
    if ico:
        matches = [c for c in companies if _only_digits(c.ico) == ico]
        if len(matches) == 1:
            return matches[0]
    dic_digits = _dic_digits(dic)
    if dic_digits:
        matches = [c for c in companies if _dic_digits(c.dic) == dic_digits]
        if len(matches) == 1:
            return matches[0]
    if name:
        scored = [
            (difflib.SequenceMatcher(None, name.lower(), c.name.lower()).ratio(), c)
            for c in companies
        ]
        best = [c for score, c in scored if score >= 0.85]
        if len(best) == 1:
            return best[0]
    return None


def to_decimal(value: Decimal | float | int | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_dates_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        # Czech format D.M.YYYY
        parts = re.split(r"[.]", value)
        if len(parts) == 3:
            try:
                return date(int(parts[2]), int(parts[1]), int(parts[0]))
            except ValueError:
                return None
    return None
