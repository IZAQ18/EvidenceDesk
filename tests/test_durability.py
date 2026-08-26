from __future__ import annotations

import hashlib
import json

import pytest

from app.config import Settings
from app.db import Database
from app.durability import DurabilityError, _load_baseline, _s3_controls, create_manifest, run
from app.ingestion import ExtractedDocument, ExtractedPage, chunk_pages
from app.models import Principal
from app.repository import Repository
from app.storage import LocalDocumentStorage, S3DocumentStorage

MANIFEST_KEY = "durability-test-manifest-key-with-32-characters"


class ControlPlaneS3Client:
    def __init__(self, *, versioning: str = "Enabled"):
        self.versioning = versioning

    def get_bucket_versioning(self, **kwargs):
        del kwargs
        return {"Status": self.versioning}

    def get_bucket_encryption(self, **kwargs):
        del kwargs
        return {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": "alias/evidencedesk-test",
                        }
                    }
                ]
            }
        }

    def get_public_access_block(self, **kwargs):
        del kwargs
        return {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }


def durability_fixture(tmp_path):
    database_path = tmp_path / "durability.db"
    originals_path = tmp_path / "originals"
    database = Database(database_path)
    database.initialize()
    storage = LocalDocumentStorage(originals_path)
    repository = Repository(database)
    content = b"Synthetic recovery policy grants three days for a documented emergency."
    extracted = ExtractedDocument(
        filename="private-recovery-policy.txt",
        content_type="text/plain",
        content=content,
        pages=[ExtractedPage(page_number=1, text=content.decode())],
        sha256=hashlib.sha256(content).hexdigest(),
        extraction_method="durability-test",
    )
    principal = Principal(
        subject="durability-admin",
        tenant_id="durability-workspace",
        role="admin",
        clearances=["public", "internal", "confidential"],
    )
    reference = storage.save(
        principal.tenant_id,
        "durability-document",
        ".txt",
        content,
        "text/plain",
    )
    repository.add_document(
        extracted,
        chunk_pages(extracted.pages),
        "confidential",
        "Synthetic durability test",
        principal,
        document_id="durability-document",
        storage_path=reference,
    )
    settings = Settings(
        _env_file=None,
        env="test",
        db_path=database_path,
        document_store_path=originals_path,
        ingestion_worker_enabled=False,
    )
    return database, storage, settings, reference, content


def test_manifest_fingerprints_database_and_verifies_originals_without_content(tmp_path):
    database, storage, _, reference, content = durability_fixture(tmp_path)
    manifest = create_manifest(
        database, storage, durable_target=False, manifest_key=MANIFEST_KEY
    )
    serialized = json.dumps(manifest)

    assert manifest["database"]["table_rows"]["documents"] == 1
    assert manifest["database"]["table_rows"]["chunks"] == 1
    assert manifest["originals"]["verified"] == 1
    assert manifest["originals"]["bytes_verified"] == len(content)
    assert manifest["fingerprint_algorithm"] == "hmac-sha256"
    assert manifest["manifest_digest"]
    assert "private-recovery-policy.txt" not in serialized
    assert content.decode() not in serialized
    assert reference not in serialized


def test_snapshot_and_restore_verification_detects_database_drift(tmp_path):
    database, _, settings, _, _ = durability_fixture(tmp_path)
    baseline_path = tmp_path / "acceptance" / "before.json"
    verified_path = tmp_path / "acceptance" / "after.json"
    baseline = run(
        mode="snapshot",
        settings=settings,
        output=baseline_path,
        allow_local=True,
    )
    verified = run(
        mode="verify",
        settings=settings,
        output=verified_path,
        baseline_path=baseline_path,
        allow_local=True,
    )
    assert verified["verified"] is True
    assert verified["baseline_manifest_digest"] == baseline["manifest_digest"]

    with database.connect() as connection:
        connection.execute(
            "UPDATE document_pages SET text = ? WHERE document_id = ? AND page_number = 1",
            ("Unexpected restored text", "durability-document"),
        )
    mismatch = run(
        mode="verify",
        settings=settings,
        output=verified_path,
        baseline_path=baseline_path,
        allow_local=True,
    )
    assert mismatch["verified"] is False
    assert "database fingerprint" in mismatch["mismatches"]


def test_manifest_rejects_modified_original_and_tampered_baseline(tmp_path):
    database, storage, _, reference, content = durability_fixture(tmp_path)
    storage.path_for(reference).write_bytes(b"x" * len(content))
    with pytest.raises(DurabilityError, match="SHA-256"):
        create_manifest(database, storage, durable_target=False, manifest_key=MANIFEST_KEY)

    _, _, settings, _, _ = durability_fixture(tmp_path / "second")
    baseline_path = tmp_path / "baseline.json"
    run(
        mode="snapshot",
        settings=settings,
        output=baseline_path,
        allow_local=True,
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["database"]["total_rows"] += 1
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(DurabilityError, match="digest is invalid"):
        _load_baseline(
            baseline_path,
            "local-durability-rehearsal-key-not-for-managed-acceptance",
        )
    with pytest.raises(DurabilityError, match="different durability manifest key"):
        _load_baseline(baseline_path, "a-different-durability-manifest-key-value")


def test_managed_acceptance_fails_closed_for_local_target(tmp_path):
    settings = Settings(
        _env_file=None,
        env="production",
        db_path=tmp_path / "local.db",
        document_store_path=tmp_path / "originals",
    )
    with pytest.raises(DurabilityError, match="production PostgreSQL"):
        run(
            mode="snapshot",
            settings=settings,
            output=tmp_path / "report.json",
        )


def test_s3_control_acceptance_requires_versioning_kms_and_public_block():
    accepted = S3DocumentStorage(
        bucket="evidencedesk-private",
        expected_bucket_owner="123456789012",
        client=ControlPlaneS3Client(),
    )
    controls = _s3_controls(accepted)
    assert all(controls.values())

    unversioned = S3DocumentStorage(
        bucket="evidencedesk-private",
        expected_bucket_owner="123456789012",
        client=ControlPlaneS3Client(versioning="Suspended"),
    )
    with pytest.raises(DurabilityError, match="controls are incomplete"):
        _s3_controls(unversioned)


def test_managed_acceptance_requires_operator_manifest_key(tmp_path):
    settings = Settings(
        _env_file=None,
        env="production",
        database_backend="postgres",
        database_url="postgresql://user:password@db.example:5432/evidencedesk?sslmode=require",
        document_store_backend="s3",
        s3_bucket="evidencedesk-private",
        s3_kms_key_id="alias/evidencedesk-data",
        s3_expected_bucket_owner="123456789012",
    )
    with pytest.raises(DurabilityError, match="DURABILITY_MANIFEST_KEY"):
        run(mode="snapshot", settings=settings, output=tmp_path / "report.json")


def test_acceptance_limits_must_be_positive(tmp_path):
    _, _, settings, _, _ = durability_fixture(tmp_path)
    with pytest.raises(DurabilityError, match="limits must be positive"):
        run(
            mode="snapshot",
            settings=settings,
            output=tmp_path / "report.json",
            allow_local=True,
            max_bytes=0,
        )
