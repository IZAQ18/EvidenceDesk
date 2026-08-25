from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import PostgresDatabase, build_database
from app.storage import LocalDocumentStorage, S3DocumentStorage

TABLE_ORDER = (
    "users",
    "workspaces",
    "workspace_members",
    "auth_sessions",
    "account_tokens",
    "workspace_invitations",
    "documents",
    "chunks",
    "document_pages",
    "conversations",
    "messages",
    "feedback",
    "audit_events",
    "payment_events",
    "ingestion_jobs",
    "answer_diagnostics",
    "model_usage_daily",
    "model_usage_events",
)
SEQUENCE_TABLES = (("chunks", "id"), ("audit_events", "id"))
SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _open_source(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"Source SQLite database does not exist: {resolved}")
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not SAFE_IDENTIFIER.fullmatch(table):
        raise RuntimeError("Unsafe table identifier")
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()]


def _source_summary(connection: sqlite3.Connection) -> dict[str, int]:
    available = _source_tables(connection)
    return {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in TABLE_ORDER
        if table in available
    }


def _ensure_quiescent(connection: sqlite3.Connection, tables: set[str]) -> None:
    if "ingestion_jobs" not in tables:
        return
    active = connection.execute(
        "SELECT COUNT(*) FROM ingestion_jobs WHERE status IN ('queued','processing')"
    ).fetchone()[0]
    if active:
        raise RuntimeError(
            "Stop ingestion and wait for every queued/processing job before migration"
        )


def _migration_id(value: str | None) -> str:
    candidate = value or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", candidate):
        raise RuntimeError(
            "Migration id must contain 6-64 letters, numbers, dashes, or underscores"
        )
    return candidate


def _storage_records(
    connection: sqlite3.Connection, tables: set[str]
) -> list[dict[str, str | int | None]]:
    records: list[dict[str, str | int | None]] = []
    if "documents" in tables:
        records.extend(
            dict(row)
            for row in connection.execute(
                """SELECT storage_path, tenant_id, id document_id, filename,
                          content_type, sha256, size_bytes
                   FROM documents WHERE storage_path IS NOT NULL"""
            ).fetchall()
        )
    if "ingestion_jobs" in tables:
        records.extend(
            dict(row)
            for row in connection.execute(
                """SELECT storage_path, tenant_id, document_id, filename,
                          content_type, sha256, size_bytes
                   FROM ingestion_jobs WHERE storage_path IS NOT NULL"""
            ).fetchall()
        )
    unique: dict[str, dict[str, str | int | None]] = {}
    for record in records:
        reference = str(record["storage_path"])
        unique.setdefault(reference, record)
    return list(unique.values())


def _verify_local_originals(
    records: Iterable[dict[str, str | int | None]], storage: LocalDocumentStorage
) -> tuple[int, int]:
    count = 0
    total_bytes = 0
    for record in records:
        reference = str(record["storage_path"])
        path = storage.path_for(reference)
        expected_size = int(record["size_bytes"] or 0)
        expected_digest = str(record["sha256"] or "")
        if expected_size and path.stat().st_size != expected_size:
            raise RuntimeError(f"Original size mismatch for document {record['document_id']}")
        if expected_digest and hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
            raise RuntimeError(f"Original digest mismatch for document {record['document_id']}")
        count += 1
        total_bytes += path.stat().st_size
    return count, total_bytes


def _copy_originals(
    records: Iterable[dict[str, str | int | None]],
    source: LocalDocumentStorage,
    target: S3DocumentStorage,
) -> dict[str, str]:
    references: dict[str, str] = {}
    for record in records:
        old_reference = str(record["storage_path"])
        content = b"".join(source.stream(old_reference))
        expected_size = int(record["size_bytes"] or 0)
        expected_digest = str(record["sha256"] or "")
        if expected_size and len(content) != expected_size:
            raise RuntimeError(f"Original size mismatch for document {record['document_id']}")
        if expected_digest and hashlib.sha256(content).hexdigest() != expected_digest:
            raise RuntimeError(f"Original digest mismatch for document {record['document_id']}")
        extension = Path(str(record["filename"])).suffix
        new_reference = target.save(
            str(record["tenant_id"]),
            str(record["document_id"]),
            extension,
            content,
            str(record["content_type"] or "application/octet-stream"),
        )
        copied = b"".join(target.stream(new_reference))
        if copied != content:
            raise RuntimeError(f"S3 round-trip mismatch for document {record['document_id']}")
        references[old_reference] = new_reference
    return references


def _target_columns(connection: Any, table: str) -> set[str]:
    return {
        row["column_name"]
        for row in connection.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name = ?""",
            (table,),
        ).fetchall()
    }


def _ensure_empty_target(connection: Any) -> None:
    occupied: list[str] = []
    for table in TABLE_ORDER:
        if connection.execute(f'SELECT COUNT(*) count FROM "{table}"').fetchone()["count"]:
            occupied.append(table)
    if occupied:
        raise RuntimeError("Target PostgreSQL database is not empty: " + ", ".join(occupied))


def _copy_database(
    source: sqlite3.Connection,
    target: PostgresDatabase,
    path_map: dict[str, str],
) -> dict[str, int]:
    available = _source_tables(source)
    copied_counts: dict[str, int] = {}
    with target.connect() as connection:
        _ensure_empty_target(connection)
        for table in TABLE_ORDER:
            if table not in available:
                copied_counts[table] = 0
                continue
            rows = _rows(source, table)
            target_columns = _target_columns(connection, table)
            source_columns = [
                row["name"] for row in source.execute(f'PRAGMA table_info("{table}")').fetchall()
            ]
            columns = [column for column in source_columns if column in target_columns]
            if any(not SAFE_IDENTIFIER.fullmatch(column) for column in columns):
                raise RuntimeError(f"Unsafe column identifier in {table}")
            names = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            statement = f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})'
            for row in rows:
                if table in {"documents", "ingestion_jobs"} and row.get("storage_path"):
                    old_reference = str(row["storage_path"])
                    if old_reference not in path_map:
                        raise RuntimeError(f"Original was not migrated for {table}")
                    row["storage_path"] = path_map[old_reference]
                connection.execute(statement, tuple(row[column] for column in columns))
            copied_counts[table] = len(rows)
        for table, column in SEQUENCE_TABLES:
            connection.execute(
                f"""SELECT setval(
                    pg_get_serial_sequence('{table}', '{column}'),
                    COALESCE((SELECT MAX({column}) FROM {table}), 1),
                    EXISTS(SELECT 1 FROM {table})
                )"""
            )
        for table, expected in copied_counts.items():
            actual = connection.execute(f'SELECT COUNT(*) count FROM "{table}"').fetchone()["count"]
            if actual != expected:
                raise RuntimeError(
                    f"Row-count verification failed for {table}: {actual} != {expected}"
                )
    return copied_counts


def migrate(
    *,
    source_db_path: Path,
    source_originals_path: Path,
    settings: Settings,
    execute: bool,
    migration_id: str | None = None,
) -> dict[str, Any]:
    source = _open_source(source_db_path)
    originals_root = source_originals_path.resolve()
    if not originals_root.is_dir():
        source.close()
        raise RuntimeError(f"Source originals directory does not exist: {originals_root}")
    source_storage = LocalDocumentStorage(originals_root)
    try:
        tables = _source_tables(source)
        if not {"documents", "chunks"} <= tables:
            raise RuntimeError("Source database is not an EvidenceDesk database")
        _ensure_quiescent(source, tables)
        summary = _source_summary(source)
        storage_records = _storage_records(source, tables)
        original_count, original_bytes = _verify_local_originals(storage_records, source_storage)
        result: dict[str, Any] = {
            "mode": "execute" if execute else "dry-run",
            "source_rows": summary,
            "originals": original_count,
            "original_bytes": original_bytes,
            "source_preserved": True,
        }
        if not execute:
            return result
        if settings.database_backend != "postgres":
            raise RuntimeError("Migration target must use PostgreSQL")
        if settings.document_store_backend != "s3":
            raise RuntimeError("Migration target must use private S3 storage")
        target_database = build_database(settings)
        if not isinstance(target_database, PostgresDatabase):
            raise RuntimeError("Migration target database is invalid")
        settings.validate_storage()
        assert settings.s3_bucket
        unique_prefix = f"{settings.s3_prefix.strip('/')}/migration-{_migration_id(migration_id)}"
        target_storage = S3DocumentStorage(
            bucket=settings.s3_bucket,
            prefix=unique_prefix,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            kms_key_id=(
                settings.s3_kms_key_id.get_secret_value() if settings.s3_kms_key_id else None
            ),
            expected_bucket_owner=settings.s3_expected_bucket_owner,
        )
        target_database.initialize()
        try:
            with target_database.connect() as connection:
                _ensure_empty_target(connection)
            target_storage.ping()
            path_map = _copy_originals(storage_records, source_storage, target_storage)
            copied = _copy_database(source, target_database, path_map)
            result.update(
                {
                    "target_rows": copied,
                    "target_prefix": unique_prefix,
                    "verified": True,
                }
            )
            return result
        finally:
            target_database.close()
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely inspect or migrate EvidenceDesk SQLite/local data to PostgreSQL/S3."
    )
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--source-originals", type=Path, required=True)
    parser.add_argument("--migration-id")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the copy. Without this flag the command is read-only.",
    )
    args = parser.parse_args()
    report = migrate(
        source_db_path=args.source_db,
        source_originals_path=args.source_originals,
        settings=Settings(),
        execute=args.execute,
        migration_id=args.migration_id,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
