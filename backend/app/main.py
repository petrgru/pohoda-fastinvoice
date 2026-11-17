import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlmodel import Session
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core.config import settings
from app.core.db import engine
from app.services import invoices as invoice_service

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.FASTAPI_ENV != "development":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


def _export_watchdog() -> None:
    while True:
        time.sleep(settings.EXPORT_WATCHDOG_SECONDS)
        try:
            with Session(engine) as session:
                invoice_service.release_stale_export_claims(session)
        except Exception:  # noqa: BLE001 - the loop must survive any single pass
            logger.exception("export watchdog pass failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # documents whose parsing was cut off by a restart would otherwise wait
    # in the queue forever - pick them back up before serving the first request
    if settings.REPARSE_ON_STARTUP:
        invoice_service.recover_stuck_invoices()
        with Session(engine) as session:
            invoice_service.release_stale_export_claims(session)
        threading.Thread(target=_export_watchdog, daemon=True).start()
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

app.include_router(api_router, prefix=settings.API_V1_STR)
app.frontend("/", directory=FRONTEND_DIR)
