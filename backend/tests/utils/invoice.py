"""Test helpers for invoice uploads and factories."""

import hashlib
import random
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlmodel import Session

from app.models import (
    Company,
    Invoice,
    InvoiceState,
    ParsedInvoice,
    VatItem,
)
from app.services import invoices as invoice_service


def make_pdf_bytes(lines: list[str]) -> bytes:
    """Build a minimal valid one-page PDF containing the given text lines."""
    content_lines = ["BT /F1 11 Tf"]
    y = 750
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content_lines.append(f"1 0 0 1 50 {y} Tm ({escaped}) Tj")
        y -= 16
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_position}\n%%EOF\n".encode()
    )
    return bytes(out)


SAMPLE_PDF_TEXT = [
    "FAKTURA 2024000123",
    "Dodavatel: TEST SUPPLIER s.r.o.",
    "ICO: 47123737  DIC: CZ47123737",
    "ucetni ustav 123456789/0100",
    "Datum vystaveni: 2024-03-01  Splatnost: 2024-03-15",
    "Zaklad dane 21%: 1000,00  DPH: 210,00  Celkem: 1210,00 Kc",
]


def parsed_sample(ico: str | None = None) -> ParsedInvoice:
    if ico is None:
        ico = str(random.randrange(10000000, 99999999))
    return ParsedInvoice(
        invoice_number="2024000123",
        supplier_name="TEST SUPPLIER s.r.o.",
        supplier_ico=ico,
        supplier_dic=f"CZ{ico}",
        supplier_bank_account="123456789/0100",
        recipient_name="NAŠE FIRMA s.r.o.",
        recipient_ico=ico,
        recipient_dic=f"CZ{ico}",
        document_type="invoice",
        issue_date=date(2024, 3, 1),
        due_date=date(2024, 3, 15),
        total_price=Decimal("1210.00"),
        total_vat=Decimal("210.00"),
        total_without_vat=Decimal("1000.00"),
        currency="CZK",
        vat_items=[
            VatItem(
                vat_rate="21%",
                base_price=Decimal("1000.00"),
                tax_price=Decimal("210.00"),
                total_price=Decimal("1210.00"),
            )
        ],
        confidence=0.92,
    )


def create_company(session: Session, **overrides: object) -> Company:
    data: dict = {
        "name": f"Test Supplier {uuid4().hex[:8]}",
        "ico": str(random.randrange(10000000, 99999999)),
        "bank_account": "123456789/0100",
    }
    data.update(overrides)
    company = Company(**data)  # type: ignore[arg-type]
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def create_invoice(
    session: Session,
    state: InvoiceState = InvoiceState.NEEDS_REVIEW,
    *,
    company: Company | None = None,
    approved_by: UUID | None = None,
    **overrides: object,
) -> Invoice:
    """Direct-to-DB invoice factory (bypasses upload + parsing)."""
    seed = f"{state.value}-{uuid4().hex}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    data: dict = {
        "original_filename": f"{seed[:8]}.pdf",
        "storage_key": f"test/{digest}.pdf",
        "content_type": "application/pdf",
        "file_size": 1024,
        "file_hash": digest,
        "state": state.value,
        "invoice_number": "2024000123",
        "supplier_name": "Test Supplier s.r.o.",
        "supplier_ico": "47123737",
        "total_price": Decimal("1210.00"),
        "total_vat": Decimal("210.00"),
        "total_without_vat": Decimal("1000.00"),
    }
    if company is not None:
        data["company_id"] = company.id
    if approved_by is not None:
        from app.models import get_datetime_utc

        data["approved_by_id"] = approved_by
        data["approved_at"] = get_datetime_utc()
    data.update(overrides)
    invoice = Invoice(**data)
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    invoice_service.update_search_vector(session, invoice)
    return invoice
