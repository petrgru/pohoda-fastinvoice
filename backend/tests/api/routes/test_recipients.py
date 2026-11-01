"""Recipient (own-company) detection and auto-assignment on upload."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.models import InvoiceState, MembershipRole, ParsedInvoice
from app.services import parsing
from tests.api.routes.test_company_access import add_membership, member_headers
from tests.utils.invoice import (
    SAMPLE_PDF_TEXT,
    create_company,
    make_pdf_bytes,
    parsed_sample,
)

UPLOAD_URL = "/api/v1/invoices/upload"


@pytest.fixture
def storage_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", tmp_path)
    return tmp_path


class ParserReturning:
    """Feeds a fixed ParsedInvoice to the background pipeline."""

    def __init__(self, monkeypatch, parsed: ParsedInvoice) -> None:
        monkeypatch.setattr(
            parsing,
            "extract_text",
            lambda data, content_type: "\n".join(SAMPLE_PDF_TEXT),
        )
        monkeypatch.setattr(
            parsing, "parse_invoice_text", lambda text, image=None: parsed
        )


def unique_pdf() -> bytes:
    return make_pdf_bytes(SAMPLE_PDF_TEXT + [f"poznamka-{uuid4().hex}"])


def upload(
    client: TestClient,
    headers: dict[str, str],
    company_id: str | None = None,
) -> object:
    data = {"company_id": company_id} if company_id else None
    return client.post(
        UPLOAD_URL,
        headers=headers,
        files={"file": ("faktura.pdf", unique_pdf(), "application/pdf")},
        data=data,
    )


def test_upload_with_sole_company_assigns_it(
    client: TestClient, db: Session, storage_dir, monkeypatch
) -> None:
    company = create_company(db)
    headers, email = member_headers(client, db)
    add_membership(db, email, company.id, MembershipRole.EDITOR)
    ParserReturning(monkeypatch, parsed_sample())  # recipient matches nothing

    response = upload(client, headers)
    assert response.status_code == 201
    assert response.json()["company_id"] == str(company.id)


def test_upload_with_foreign_company_is_forbidden(
    client: TestClient, db: Session, storage_dir
) -> None:
    mine = create_company(db)
    theirs = create_company(db)
    headers, email = member_headers(client, db)
    add_membership(db, email, mine.id, MembershipRole.EDITOR)

    response = upload(client, headers, company_id=str(theirs.id))
    assert response.status_code == 403


def test_upload_with_chosen_company(
    client: TestClient, db: Session, storage_dir, monkeypatch
) -> None:
    company_a = create_company(db)
    company_b = create_company(db)
    headers, email = member_headers(client, db)
    add_membership(db, email, company_a.id, MembershipRole.EDITOR)
    add_membership(db, email, company_b.id, MembershipRole.EDITOR)
    ParserReturning(monkeypatch, parsed_sample())  # recipient matches nothing

    response = upload(client, headers, company_id=str(company_b.id))
    assert response.status_code == 201
    assert response.json()["company_id"] == str(company_b.id)


def test_recipient_ico_match_among_member_companies(
    client: TestClient, db: Session, storage_dir, monkeypatch
) -> None:
    company_a = create_company(db)
    company_b = create_company(db)
    headers, email = member_headers(client, db)
    add_membership(db, email, company_a.id, MembershipRole.EDITOR)
    add_membership(db, email, company_b.id, MembershipRole.EDITOR)
    ParserReturning(monkeypatch, parsed_sample(ico=company_b.ico))

    response = upload(client, headers)
    assert response.status_code == 201
    body = client.get(
        f"/api/v1/invoices/{response.json()['id']}", headers=headers
    ).json()
    assert body["company_id"] == str(company_b.id)
    assert body["recipient_ico"] == company_b.ico


def test_unknown_recipient_stays_unassigned(
    client: TestClient, db: Session, storage_dir, monkeypatch
) -> None:
    company_a = create_company(db)
    company_b = create_company(db)
    headers, email = member_headers(client, db)
    add_membership(db, email, company_a.id, MembershipRole.EDITOR)
    add_membership(db, email, company_b.id, MembershipRole.EDITOR)
    ParserReturning(monkeypatch, parsed_sample())

    response = upload(client, headers)
    assert response.status_code == 201

    # a member of two companies without a recipient match has to choose
    body = client.get(
        f"/api/v1/invoices/{response.json()['id']}", headers=headers
    ).json()
    assert body["company_id"] is None
    assert body["state"] == InvoiceState.NEEDS_REVIEW.value


def test_recipient_never_matches_foreign_company(
    client: TestClient, db: Session, storage_dir, monkeypatch
) -> None:
    company_a = create_company(db)
    company_b = create_company(db)
    foreign = create_company(db)
    headers, email = member_headers(client, db)
    add_membership(db, email, company_a.id, MembershipRole.EDITOR)
    add_membership(db, email, company_b.id, MembershipRole.EDITOR)
    ParserReturning(monkeypatch, parsed_sample(ico=foreign.ico))

    response = upload(client, headers)
    assert response.status_code == 201
    body = client.get(
        f"/api/v1/invoices/{response.json()['id']}", headers=headers
    ).json()
    assert body["company_id"] is None
