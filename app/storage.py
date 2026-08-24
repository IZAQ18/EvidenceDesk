from __future__ import annotations

import hashlib
import mimetypes
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings

_ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}
_CHUNK_BYTES = 64 * 1024


class OriginalStorage(Protocol):
    def save(
        self,
        tenant_id: str,
        document_id: str,
        extension: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str: ...

    def stream(self, reference: str) -> Iterable[bytes]: ...

    def delete(self, reference: str | None) -> None: ...


def _safe_tenant(tenant_id: str) -> str:
    safe = "".join(char for char in tenant_id if char.isalnum() or char in "-_")[:80]
    return safe or hashlib.sha256(tenant_id.encode()).hexdigest()[:20]


def _safe_extension(extension: str) -> str:
    normalized = extension.lower()
    return normalized if normalized in _ALLOWED_EXTENSIONS else ".bin"


class LocalDocumentStorage:
    """Local original-file storage with generated paths and containment checks."""

    scheme = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _relative(reference: str) -> str:
        if reference.startswith("local:"):
            return reference.removeprefix("local:")
        if ":" in reference:
            raise ValueError("Document belongs to a different storage backend")
        return reference  # Backwards compatibility for pre-v0.9 local rows.

    def _resolve(self, reference: str) -> Path:
        relative_path = self._relative(reference)
        target = (self.root / relative_path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError("Unsafe document storage path")
        return target

    def save(
        self,
        tenant_id: str,
        document_id: str,
        extension: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        del content_type
        relative = f"{_safe_tenant(tenant_id)}/{document_id}{_safe_extension(extension)}"
        target = self._resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
        return f"local:{relative}"

    def path_for(self, reference: str) -> Path:
        path = self._resolve(reference)
        if not path.is_file():
            raise FileNotFoundError(reference)
        return path

    def stream(self, reference: str) -> Iterable[bytes]:
        path = self.path_for(reference)

        def chunks() -> Iterable[bytes]:
            with path.open("rb") as handle:
                while chunk := handle.read(_CHUNK_BYTES):
                    yield chunk

        return chunks()

    def delete(self, reference: str | None) -> None:
        if not reference:
            return
        path = self._resolve(reference)
        if path.is_file():
            path.unlink()


class S3DocumentStorage:
    """Private S3 object storage; credentials come from the standard AWS provider chain."""

    scheme = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "evidencedesk/originals",
        region: str | None = None,
        endpoint_url: str | None = None,
        kms_key_id: str | None = None,
        expected_bucket_owner: str | None = None,
        client: Any | None = None,
    ):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.kms_key_id = kms_key_id
        self.expected_bucket_owner = expected_bucket_owner
        if not bucket:
            raise ValueError("S3 bucket is required")
        if not self.prefix or ".." in self.prefix.split("/") or "\\" in self.prefix:
            raise ValueError("S3 prefix is invalid")
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("Install the S3 dependencies from requirements.txt") from exc
            client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url)
        self.client = client

    @staticmethod
    def _key(reference: str) -> str:
        if not reference.startswith("s3:"):
            raise ValueError("Document belongs to a different storage backend")
        key = reference.removeprefix("s3:")
        if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
            raise ValueError("Unsafe S3 object reference")
        return key

    def _owner(self) -> dict[str, str]:
        return (
            {"ExpectedBucketOwner": self.expected_bucket_owner}
            if self.expected_bucket_owner
            else {}
        )

    def save(
        self,
        tenant_id: str,
        document_id: str,
        extension: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        extension = _safe_extension(extension)
        key = f"{self.prefix}/{_safe_tenant(tenant_id)}/{document_id}{extension}"
        encryption = (
            {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id}
            if self.kms_key_id
            else {"ServerSideEncryption": "AES256"}
        )
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=content_type or mimetypes.guess_type(key)[0] or "application/octet-stream",
            **encryption,
            **self._owner(),
        )
        return f"s3:{key}"

    def stream(self, reference: str) -> Iterable[bytes]:
        key = self._key(reference)
        try:
            response = self.client.get_object(
                Bucket=self.bucket, Key=key, **self._owner()
            )
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if str(error.get("Code", "")) in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError(reference) from exc
            raise
        body = response["Body"]

        def chunks() -> Iterable[bytes]:
            try:
                yield from body.iter_chunks(chunk_size=_CHUNK_BYTES)
            finally:
                body.close()

        return chunks()

    def delete(self, reference: str | None) -> None:
        if not reference:
            return
        self.client.delete_object(
            Bucket=self.bucket, Key=self._key(reference), **self._owner()
        )


def build_document_storage(settings: Settings) -> OriginalStorage:
    settings.validate_storage()
    if settings.document_store_backend == "s3":
        assert settings.s3_bucket
        return S3DocumentStorage(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            kms_key_id=(
                settings.s3_kms_key_id.get_secret_value() if settings.s3_kms_key_id else None
            ),
            expected_bucket_owner=settings.s3_expected_bucket_owner,
        )
    return LocalDocumentStorage(settings.document_store_path)


# Backwards-compatible name used by existing integrations and tests.
DocumentStorage = LocalDocumentStorage
