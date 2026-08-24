from __future__ import annotations

import hashlib

import pytest

from app.config import Settings
from app.db import Database
from app.ingestion import ExtractedDocument, ExtractedPage, chunk_pages
from app.migrate_data import migrate
from app.models import Principal
from app.repository import Repository
from app.storage import LocalDocumentStorage


def migration_fixture(tmp_path):
    database_path = tmp_path / "source.db"
    originals_path = tmp_path / "originals"
    database = Database(database_path)
    database.initialize()
    repository = Repository(database)
    storage = LocalDocumentStorage(originals_path)
    content = b"Employees receive twenty paid annual leave days each calendar year."
    extracted = ExtractedDocument(
        filename="leave-policy.txt",
        content_type="text/plain",
        content=content,
        pages=[ExtractedPage(page_number=1, text=content.decode())],
        sha256=hashlib.sha256(content).hexdigest(),
        extraction_method="migration-test",
    )
    principal = Principal(
        subject="migration-admin",
        tenant_id="migration-tenant",
        role="admin",
        clearances=["public", "internal", "confidential"],
    )
    reference = storage.save(
        principal.tenant_id,
        "leave-document",
        ".txt",
        content,
        "text/plain",
    )
    repository.add_document(
        extracted,
        chunk_pages(extracted.pages),
        "internal",
        "Synthetic migration fixture",
        principal,
        document_id="leave-document",
        storage_path=reference,
    )
    return database_path, originals_path, reference, content


def test_migration_dry_run_is_read_only_and_reports_counts(tmp_path):
    database_path, originals_path, reference, content = migration_fixture(tmp_path)
    original_path = LocalDocumentStorage(originals_path).path_for(reference)
    before = original_path.read_bytes()
    report = migrate(
        source_db_path=database_path,
        source_originals_path=originals_path,
        settings=Settings(_env_file=None),
        execute=False,
    )
    assert report["mode"] == "dry-run"
    assert report["source_rows"]["documents"] == 1
    assert report["source_rows"]["chunks"] == 1
    assert report["originals"] == 1
    assert report["original_bytes"] == len(content)
    assert report["source_preserved"] is True
    assert original_path.read_bytes() == before


def test_migration_refuses_missing_or_modified_original(tmp_path):
    database_path, originals_path, reference, content = migration_fixture(tmp_path)
    original_path = LocalDocumentStorage(originals_path).path_for(reference)
    original_path.write_bytes(b"x" * len(content))
    with pytest.raises(RuntimeError, match="digest mismatch"):
        migrate(
            source_db_path=database_path,
            source_originals_path=originals_path,
            settings=Settings(_env_file=None),
            execute=False,
        )


def test_execute_requires_postgres_and_s3_targets(tmp_path):
    database_path, originals_path, _, _ = migration_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        migrate(
            source_db_path=database_path,
            source_originals_path=originals_path,
            settings=Settings(_env_file=None),
            execute=True,
        )
