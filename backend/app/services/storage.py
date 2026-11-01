"""File storage backends: local filesystem (development) and S3 (production)."""

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import settings

ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tif",
}


class FileTooLargeError(ValueError):
    pass


class UnsupportedFileTypeError(ValueError):
    pass


class StorageBackend(ABC):
    @abstractmethod
    def save(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def open(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class LocalStorage(StorageBackend):
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError("Invalid storage key")
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def open(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage(StorageBackend):
    def __init__(self) -> None:
        if not settings.S3_BUCKET:
            raise ValueError("S3_BUCKET must be set for the s3 storage backend")
        client_kwargs: dict[str, Any] = {
            "region_name": settings.S3_REGION,
            "config": BotoConfig(signature_version="s3v4"),
        }
        if settings.S3_ENDPOINT_URL:
            client_kwargs["endpoint_url"] = str(settings.S3_ENDPOINT_URL)
        if settings.S3_ACCESS_KEY_ID and settings.S3_SECRET_ACCESS_KEY:
            client_kwargs["aws_access_key_id"] = settings.S3_ACCESS_KEY_ID
            client_kwargs["aws_secret_access_key"] = settings.S3_SECRET_ACCESS_KEY
        self.bucket = settings.S3_BUCKET
        self._client = boto3.client("s3", **client_kwargs)

    def save(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def open(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return bytes(obj["Body"].read())

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> StorageBackend:
    if settings.STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorage(settings.LOCAL_STORAGE_PATH)


def prepare_invoice_file(data: bytes, content_type: str) -> tuple[str, str]:
    """Validate an upload and derive its content-addressed key + sha256,
    WITHOUT writing it yet (so duplicates can be rejected before touching disk).
    """
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedFileTypeError(
            f"Nepodporovaný typ souboru: {content_type}. "
            f"Povolené: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise FileTooLargeError(f"Soubor větší než {settings.MAX_UPLOAD_SIZE_MB} MB")
    digest = hashlib.sha256(data).hexdigest()
    extension = ALLOWED_CONTENT_TYPES[content_type]
    key = f"{digest[:2]}/{digest}{extension}"
    return key, digest


def store_invoice_file(
    data: bytes, content_type: str, _filename: str
) -> tuple[str, str]:
    """Validate and store an uploaded file, returning (storage_key, sha256_hex)."""
    key, digest = prepare_invoice_file(data, content_type)
    get_storage().save(key, data)
    return key, digest
