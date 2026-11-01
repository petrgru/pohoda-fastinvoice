from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.models import AuditLog, Company, ExportBatch, Invoice, User
from app.services import vat
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    with Session(engine) as session:
        init_db(session)
        yield session
        session.rollback()
        for model in (AuditLog, Invoice, Company, ExportBatch, User):
            session.execute(delete(model))
        session.commit()


@pytest.fixture(autouse=True)
def no_live_vat_service(monkeypatch: pytest.MonkeyPatch):
    """Never hit VatComply from tests; specific tests re-enable it with a fake."""
    monkeypatch.setattr(settings, "VATCOMPLY_ENABLED", False)
    monkeypatch.setattr(settings, "MEILISEARCH_ENABLED", False)
    # DEVELOP=yes in the developer's .env must not change test semantics
    monkeypatch.setattr(settings, "DEVELOP", False)
    vat._cache.clear()
    yield
    vat._cache.clear()
    vat._fetch_locks.clear()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
