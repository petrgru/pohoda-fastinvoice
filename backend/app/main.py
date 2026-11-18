import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import sentry_sdk
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sentry_sdk.types import Event, Hint
from sqlmodel import Session
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core.config import settings
from app.core.db import engine
from app.core.logging import SENSITIVE_KEYS, RequestLogMiddleware, configure_logging
from app.services import invoices as invoice_service

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


configure_logging()


def _scrub_sentry(event: Event, hint: Hint) -> Event | None:  # noqa: ARG001
    """Keep secrets out of Sentry payloads (same red line as the log pipeline)."""
    # sentry types the event as a TypedDict; we only touch request headers
    request = cast("dict[str, Any]", event.get("request") or {})
    headers = cast("dict[str, Any]", request.get("headers") or {})
    for key in list(headers):
        if key.lower() in SENSITIVE_KEYS:
            headers[key] = "***"
    return event


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(
        dsn=str(settings.SENTRY_DSN),
        enable_tracing=True,
        # the structlog integration would double-render our pipeline; opt in
        # deliberately later if Sentry events from log records are wanted
        auto_enabling_integrations=False,
        before_send=_scrub_sentry,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # documents whose parsing was cut off by a restart would otherwise wait
    # in the queue forever - pick them back up before serving the first
    # request; in queue mode this only re-enqueues, the periodic jobs
    # (app.tasks) keep claims and stalled parses covered from here on
    if settings.REPARSE_ON_STARTUP:
        invoice_service.recover_stuck_invoices()
        with Session(engine) as session:
            invoice_service.release_stale_export_claims(session)
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_HOST],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# added later = runs earlier: the correlation id must already be bound when
# RequestLogMiddleware logs, and user/invoice context bound deeper down
# propagates back up because both middlewares are pure ASGI (no task hop)
app.add_middleware(RequestLogMiddleware)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(api_router, prefix=settings.API_V1_STR)
app.frontend("/", directory=FRONTEND_DIR)
