from __future__ import annotations

import pytest

from app.config import Settings
from app.storage import LocalDocumentStorage, S3DocumentStorage, build_document_storage


class FakeBody:
    def __init__(self, content: bytes):
        self.content = content
        self.closed = False

    def iter_chunks(self, chunk_size: int):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class MissingObjectError(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3Client:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[dict] = []
        self.delete_calls: list[dict] = []
        self.last_body: FakeBody | None = None
        self.head_calls: list[dict] = []
        self.copy_calls: list[dict] = []

    def head_bucket(self, **kwargs):
        self.head_calls.append(kwargs)

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def get_object(self, **kwargs):
        try:
            content = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        except KeyError as exc:
            raise MissingObjectError from exc
        self.last_body = FakeBody(content)
        return {"Body": self.last_body}

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)

    def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)
        source = kwargs["CopySource"]
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = self.objects[
            (source["Bucket"], source["Key"])
        ]


def test_local_storage_uses_typed_references_and_reads_legacy_rows(tmp_path):
    storage = LocalDocumentStorage(tmp_path / "originals")
    reference = storage.save("tenant-a", "document-id", ".txt", b"verified evidence")
    assert reference == "local:tenant-a/document-id.txt"
    assert b"".join(storage.stream(reference)) == b"verified evidence"
    assert b"".join(storage.stream("tenant-a/document-id.txt")) == b"verified evidence"
    with pytest.raises(ValueError, match="different storage backend"):
        list(storage.stream("s3:another/object.txt"))


def test_local_quarantine_requires_explicit_promotion(tmp_path):
    storage = LocalDocumentStorage(tmp_path / "originals")
    staged = storage.stage("tenant-a", "job-id", ".txt", b"quarantined evidence")
    assert staged == "local:quarantine/tenant-a/job-id.txt"
    promoted = storage.promote(staged, "tenant-a", "document-id", ".txt")
    assert promoted == "local:tenant-a/document-id.txt"
    assert b"".join(storage.stream(staged)) == b"quarantined evidence"
    assert b"".join(storage.stream(promoted)) == b"quarantined evidence"
    storage.delete(staged)
    assert b"".join(storage.stream(promoted)) == b"quarantined evidence"


def test_s3_storage_encrypts_streams_and_deletes_private_objects():
    client = FakeS3Client()
    storage = S3DocumentStorage(
        bucket="evidencedesk-private",
        prefix="evidence/originals",
        expected_bucket_owner="123456789012",
        client=client,
    )
    reference = storage.save(
        "workspace-123",
        "document-456",
        ".pdf",
        b"private pdf bytes",
        "application/pdf",
    )
    assert reference == "s3:evidence/originals/workspace-123/document-456.pdf"
    uploaded = client.put_calls[0]
    assert uploaded["Bucket"] == "evidencedesk-private"
    assert uploaded["ServerSideEncryption"] == "AES256"
    assert uploaded["ExpectedBucketOwner"] == "123456789012"
    assert "ACL" not in uploaded
    assert storage.ping() is True
    assert client.head_calls[0]["ExpectedBucketOwner"] == "123456789012"

    assert b"".join(storage.stream(reference)) == b"private pdf bytes"
    assert client.last_body and client.last_body.closed is True
    storage.delete(reference)
    assert client.delete_calls[0]["ExpectedBucketOwner"] == "123456789012"
    with pytest.raises(FileNotFoundError):
        list(storage.stream(reference))


def test_s3_kms_and_reference_validation():
    client = FakeS3Client()
    storage = S3DocumentStorage(
        bucket="evidencedesk-private",
        kms_key_id="alias/evidencedesk-documents",
        client=client,
    )
    storage.save("tenant", "document", ".txt", b"content")
    uploaded = client.put_calls[0]
    assert uploaded["ServerSideEncryption"] == "aws:kms"
    assert uploaded["SSEKMSKeyId"] == "alias/evidencedesk-documents"
    with pytest.raises(ValueError, match="different storage backend"):
        list(storage.stream("local:tenant/document.txt"))
    with pytest.raises(ValueError, match="Unsafe S3 object reference"):
        list(storage.stream("s3:../private.txt"))


def test_s3_quarantine_promotion_keeps_source_until_commit_cleanup():
    client = FakeS3Client()
    storage = S3DocumentStorage(
        bucket="evidencedesk-private",
        prefix="evidence/originals",
        expected_bucket_owner="123456789012",
        kms_key_id="alias/evidencedesk-documents",
        client=client,
    )
    staged = storage.stage("tenant", "job", ".pdf", b"private pdf", "application/pdf")
    promoted = storage.promote(staged, "tenant", "document", ".pdf", "application/pdf")
    assert staged == "s3:evidence/originals/quarantine/tenant/job.pdf"
    assert promoted == "s3:evidence/originals/tenant/document.pdf"
    assert b"".join(storage.stream(staged)) == b"private pdf"
    assert b"".join(storage.stream(promoted)) == b"private pdf"
    assert client.copy_calls[0]["ExpectedSourceBucketOwner"] == "123456789012"


def test_s3_configuration_fails_closed(tmp_path):
    missing_bucket = Settings(
        _env_file=None,
        document_store_backend="s3",
        db_path=tmp_path / "missing.db",
    )
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        build_document_storage(missing_bucket)

    insecure_endpoint = Settings(
        _env_file=None,
        env="production",
        document_store_backend="s3",
        s3_bucket="evidencedesk-private",
        s3_endpoint_url="http://object-storage.internal",
        db_path=tmp_path / "insecure.db",
    )
    with pytest.raises(RuntimeError, match="must use HTTPS"):
        build_document_storage(insecure_endpoint)

    with pytest.raises(ValueError, match="prefix is invalid"):
        S3DocumentStorage(
            bucket="evidencedesk-private", prefix="../escape", client=FakeS3Client()
        )


def test_local_storage_readiness(tmp_path):
    storage = LocalDocumentStorage(tmp_path / "originals")
    assert storage.ping() is True
