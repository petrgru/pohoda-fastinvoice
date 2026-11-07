"""Test configuration.

The suite runs against a dedicated ``<dbname>_test`` database derived from
``DATABASE_URL`` (override with ``TEST_DATABASE_URL``). Without this isolation
the session teardown below would wipe the developer's real development
database – the template assumes a disposable one.
"""

import os
from collections.abc import Generator
from pathlib import Path

# --- redirect to the test database BEFORE any app module reads settings ---
import psycopg  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def _ensure_test_database() -> str:
    """Create <current dbname>_test if missing; return its URL."""
    if url := os.getenv("TEST_DATABASE_URL"):
        return url
    base = os.getenv("DATABASE_URL") or str(
        dotenv_values(Path(__file__).resolve().parents[2] / ".env").get(
            "DATABASE_URL", ""
        )
    )
    if not base:
        raise RuntimeError("DATABASE_URL or TEST_DATABASE_URL must be set for tests")
    base = base.replace("postgres://", "postgresql://", 1).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    parsed = make_url(base)
    test_name = f"{parsed.database}_test"
    with psycopg.connect(
        host=parsed.host,
        port=parsed.port or 5432,
        user=parsed.username,
        password=parsed.password,
        dbname="postgres",
    ) as admin:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_name,))
            if not cursor.fetchone():
                # CREATE DATABASE cannot run in a transaction (autocommit on)
                cursor.execute(f'CREATE DATABASE "{test_name}"')
    return (
        parsed.set(database=test_name)
        .render_as_string(hide_password=False)
        .replace("postgresql://", "postgresql+psycopg://", 1)
    )


os.environ["DATABASE_URL"] = _ensure_test_database()
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, delete  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.db import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    ExportBatch,
    Invoice,
    RecipientCompany,
    User,
)
from app.services import vat  # noqa: E402
from tests.utils.user import authentication_token_from_email  # noqa: E402
from tests.utils.utils import get_superuser_token_headers  # noqa: E402

if not os.getenv("TEST_DATABASE_URL"):
    db_name = make_url(str(settings.DATABASE_URL)).database or ""
    assert db_name.endswith("_test"), "refusing to run tests against the main database"


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    # fresh schema every run: no alembic needed, no stale columns possible
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        init_db(session)
        yield session
        session.rollback()
        for model in (AuditLog, Invoice, RecipientCompany, ExportBatch, User):
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
