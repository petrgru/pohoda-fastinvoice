import warnings
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    FASTAPI_ENV: Literal["development"] | None = None

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    DATABASE_URL: PostgresDsn

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
        database_url = str(value)
        for scheme in ("postgres://", "postgresql://"):
            if database_url.startswith(scheme):
                return database_url.replace(scheme, "postgresql+psycopg://", 1)
        return database_url

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    # --- Invoice storage --------------------------------------------------
    # "local" stores files under LOCAL_STORAGE_PATH (development),
    # "s3" stores them in an S3-compatible bucket (production).
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    LOCAL_STORAGE_PATH: Path = Path("./storage/invoices")
    S3_BUCKET: str | None = None
    S3_ENDPOINT_URL: HttpUrl | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_REGION: str = "us-east-1"
    MAX_UPLOAD_SIZE_MB: int = 20

    # --- Invoice parsing (OpenAI-compatible LLM API) ------------------------
    # Works with any server exposing /chat/completions (OpenAI, llama.cpp,
    # vLLM, Ollama in --api-compat mode, …). API key is optional for local
    # servers; when set it is sent as `Authorization: Bearer …`.
    LLM_BASE_URL: str = "http://192.168.22.154:18300/v1"
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "qwen3.8-flash-next"
    # Vision model used for image-only documents; defaults to LLM_MODEL.
    LLM_VISION_MODEL: str | None = None
    LLM_TIMEOUT_SECONDS: int = 180
    # guided (schema-constrained) decoding can stall on multi-page scans; give
    # that attempt a tighter budget and fall back to plain JSON prompting
    LLM_STRUCTURED_TIMEOUT_SECONDS: int = 150
    # Path to tesseract binary for OCR of scans/photos; None disables OCR.
    TESSERACT_PATH: str | None = "tesseract"

    # --- Export API for the Pohoda connector app ----------------------------
    # When unset, /api/v1/export/* endpoints return 503.
    EXPORT_API_KEY: str | None = None

    # --- VAT validation (VatComply / VIES) -----------------------------------
    # DIČ is validated against VatComply (no API key needed, 2 req/s limit).
    # When the service is unreachable the check is skipped (fail-open).
    # `DEVELOP=yes` in .env enables destructive development-only features
    DEVELOP: bool = False
    MEILISEARCH_ENABLED: bool = True
    MEILISEARCH_URL: str = "http://localhost:7700"
    MEILISEARCH_MASTER_KEY: str = ""
    VATCOMPLY_BASE_URL: str = "https://api.vatcomply.com"
    VATCOMPLY_ENABLED: bool = True
    VATCOMPLY_TIMEOUT_SECONDS: float = 5.0

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.FASTAPI_ENV == "development":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        for host in self.DATABASE_URL.hosts():
            self._check_default_secret("DATABASE_URL password", host["password"])
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore # ty: ignore[unused-ignore-comment]
