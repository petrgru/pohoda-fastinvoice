from fastapi import APIRouter

from app.api.routes import (
    companies,
    data,
    export,
    invoices,
    login,
    private,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(companies.router)
api_router.include_router(invoices.router)
api_router.include_router(export.router)
api_router.include_router(data.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
