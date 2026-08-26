from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import DatabaseLike, PostgresDatabase, build_database
from app.migrate_data import TABLE_ORDER
from app.storage import OriginalStorage, S3DocumentStorage, build_document_storage

REPORT_VERSION = 1
MAX_BASELINE_BYTES = 2_000_000
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "users": ("id",),
    "workspaces": ("id",),
    "workspace_members": ("workspace_id", "user_id"),
    "auth_sessions": ("token_hash",),
    "account_tokens": ("token_hash",),
    "workspace_invitations": ("id",),
    "documents": ("id",),
    "chunks": ("id",),
    "document_pages": ("document_id", "page_number"),
    "conversations": ("id",),
    "messages": ("id",),
    "feedback": ("id",),
    "audit_events": ("id",),
    "payment_events": ("event_key",),
    "ingestion_jobs": ("id",),
    "answer_diagnostics": ("message_id",),
    "model_usage_daily": ("tenant_id", "usage_date"),
    "model_usage_events": ("id",),
}


class DurabilityError(RuntimeError):
    pass


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, memoryview):
        return value.tobytes().hex()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_payload(row: Any) -> bytes:
    normalized = _canonical_value(dict(row))
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _database_snapshot(
    database: DatabaseLike, max_rows: int, manifest_key: bytes
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    fingerprints: dict[str, str] = {}
    total_rows = 0
    with database.connect() as connection:
        active = connection.execute(
            "SELECT COUNT(*) count FROM ingestion_jobs WHERE status IN ('queued','processing')"
        ).fetchone()["count"]
        if active:
            raise DurabilityError(
                "Stop ingestion and wait for queued or processing jobs before capturing a baseline"
            )
        versions = (
            [
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            if isinstance(database, PostgresDatabase)
            else []
        )
        for table in TABLE_ORDER:
            count = connection.execute(
                f'SELECT COUNT(*) count FROM "{table}"'
            ).fetchone()["count"]
            counts[table] = count
            total_rows += count
            if total_rows > max_rows:
                raise DurabilityError(
                    f"Database contains more than the configured {max_rows} acceptance rows"
                )
            order_by = ", ".join(f'"{column}"' for column in PRIMARY_KEYS[table])
            rows = connection.execute(
                f'SELECT * FROM "{table}" ORDER BY {order_by}'
            ).fetchall()
            digest = hmac.new(manifest_key, digestmod=hashlib.sha256)
            digest.update(f"{table}\n".encode())
            for row in rows:
                digest.update(_row_payload(row))
                digest.update(b"\n")
            fingerprints[table] = digest.hexdigest()
    return {
        "schema_versions": versions,
        "table_rows": counts,
        "table_fingerprints": fingerprints,
        "total_rows": total_rows,
    }


def _storage_records(database: DatabaseLike) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    statements = (
        """SELECT storage_path, sha256, size_bytes FROM documents
           WHERE storage_path IS NOT NULL""",
        """SELECT storage_path, sha256, size_bytes FROM ingestion_jobs
           WHERE storage_path IS NOT NULL""",
    )
    with database.connect() as connection:
        for statement in statements:
            for row in connection.execute(statement).fetchall():
                record = dict(row)
                reference = str(record["storage_path"])
                existing = records.get(reference)
                if existing and (
                    existing["sha256"] != record["sha256"]
                    or existing["size_bytes"] != record["size_bytes"]
                ):
                    raise DurabilityError("One original reference has conflicting database metadata")
                records[reference] = record
    return [records[key] for key in sorted(records)]


def _verify_originals(
    database: DatabaseLike,
    storage: OriginalStorage,
    *,
    max_originals: int,
    max_bytes: int,
    manifest_key: bytes,
) -> dict[str, Any]:
    records = _storage_records(database)
    if len(records) > max_originals:
        raise DurabilityError(
            f"Storage contains more than the configured {max_originals} acceptance originals"
        )
    expected_bytes = sum(max(0, int(record["size_bytes"] or 0)) for record in records)
    if expected_bytes > max_bytes:
        raise DurabilityError(
            f"Originals exceed the configured {max_bytes} acceptance-byte limit"
        )
    aggregate = hmac.new(manifest_key, digestmod=hashlib.sha256)
    actual_bytes = 0
    for record in records:
        reference = str(record["storage_path"])
        expected_digest = str(record["sha256"] or "")
        expected_size = int(record["size_bytes"] or 0)
        digest = hashlib.sha256()
        size = 0
        try:
            for chunk in storage.stream(reference):
                size += len(chunk)
                actual_bytes += len(chunk)
                if actual_bytes > max_bytes:
                    raise DurabilityError(
                        f"Originals exceed the configured {max_bytes} acceptance-byte limit"
                    )
                digest.update(chunk)
        except DurabilityError:
            raise
        except Exception as exc:
            raise DurabilityError("An original object could not be read from private storage") from exc
        actual_digest = digest.hexdigest()
        if expected_size != size:
            raise DurabilityError("An original object does not match its database size")
        if expected_digest != actual_digest:
            raise DurabilityError("An original object does not match its database SHA-256 digest")
        reference_digest = hashlib.sha256(reference.encode()).hexdigest()
        aggregate.update(f"{reference_digest}:{size}:{actual_digest}\n".encode())
    return {
        "expected": len(records),
        "verified": len(records),
        "bytes_verified": actual_bytes,
        "aggregate_fingerprint": aggregate.hexdigest(),
    }


def _s3_controls(storage: S3DocumentStorage) -> dict[str, bool | str]:
    owner = storage._owner()
    try:
        versioning = storage.client.get_bucket_versioning(
            Bucket=storage.bucket, **owner
        )
        encryption = storage.client.get_bucket_encryption(
            Bucket=storage.bucket, **owner
        )
        public_access = storage.client.get_public_access_block(
            Bucket=storage.bucket, **owner
        )
    except Exception as exc:
        raise DurabilityError(
            "S3 control inspection failed; use an operator identity with read-only bucket-control permissions"
        ) from exc
    rules = encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
    algorithms = {
        rule.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
        for rule in rules
    }
    block = public_access.get("PublicAccessBlockConfiguration", {})
    controls: dict[str, bool | str] = {
        "versioning_enabled": versioning.get("Status") == "Enabled",
        "default_kms_encryption": "aws:kms" in algorithms,
        "block_public_acls": block.get("BlockPublicAcls") is True,
        "ignore_public_acls": block.get("IgnorePublicAcls") is True,
        "block_public_policy": block.get("BlockPublicPolicy") is True,
        "restrict_public_buckets": block.get("RestrictPublicBuckets") is True,
    }
    if not all(value is True for value in controls.values()):
        raise DurabilityError("S3 versioning, KMS encryption, or public-access controls are incomplete")
    return controls


def _validate_target(settings: Settings, allow_local: bool) -> bool:
    settings.validate_database()
    settings.validate_storage()
    durable = (
        settings.env == "production"
        and settings.database_backend == "postgres"
        and settings.document_store_backend == "s3"
        and settings.s3_kms_key_id is not None
        and settings.s3_expected_bucket_owner is not None
    )
    if not durable and not allow_local:
        raise DurabilityError(
            "Acceptance requires production PostgreSQL plus private KMS-encrypted S3 with expected-owner checking"
        )
    return durable


def _manifest_digest(report: dict[str, Any], manifest_key: bytes) -> str:
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hmac.new(manifest_key, payload, hashlib.sha256).hexdigest()


def create_manifest(
    database: DatabaseLike,
    storage: OriginalStorage,
    *,
    durable_target: bool,
    max_rows: int = 1_000_000,
    max_originals: int = 10_000,
    max_bytes: int = 10_000_000_000,
    manifest_key: str,
) -> dict[str, Any]:
    key = manifest_key.encode("utf-8")
    if len(key) < 32:
        raise DurabilityError("Durability manifest key must contain at least 32 characters")
    if not database.ping() or not storage.ping():
        raise DurabilityError("Database or private original storage is not ready")
    before = _database_snapshot(database, max_rows, key)
    originals = _verify_originals(
        database,
        storage,
        max_originals=max_originals,
        max_bytes=max_bytes,
        manifest_key=key,
    )
    after = _database_snapshot(database, max_rows, key)
    if before != after:
        raise DurabilityError(
            "Database changed during capture; stop application traffic and try again"
        )
    controls: dict[str, Any] = {
        "database_ready": True,
        "original_storage_ready": True,
    }
    if durable_target:
        if not isinstance(database, PostgresDatabase) or not isinstance(
            storage, S3DocumentStorage
        ):
            raise DurabilityError("Durable acceptance target adapters are inconsistent")
        controls.update(_s3_controls(storage))
    core = {
        "report_version": REPORT_VERSION,
        "fingerprint_algorithm": "hmac-sha256",
        "durable_target": durable_target,
        "database": before,
        "originals": originals,
        "controls": controls,
        "manifest_key_fingerprint": hashlib.sha256(key).hexdigest()[:16],
    }
    return {
        "kind": "evidencedesk_durability_baseline",
        "generated_at": datetime.now(UTC).isoformat(),
        **core,
        "manifest_digest": _manifest_digest(core, key),
    }


def compare_manifest(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    if baseline.get("kind") != "evidencedesk_durability_baseline":
        raise DurabilityError("Baseline is not an EvidenceDesk durability manifest")
    if baseline.get("report_version") != REPORT_VERSION:
        raise DurabilityError("Baseline report version is unsupported")
    sections = ("durable_target", "database", "originals", "controls")
    labels = {
        "durable_target": "deployment boundary",
        "database": "database fingerprint",
        "originals": "original-storage fingerprint",
        "controls": "durability controls",
    }
    mismatches = [labels[section] for section in sections if baseline.get(section) != current.get(section)]
    return {
        "kind": "evidencedesk_restore_verification",
        "generated_at": datetime.now(UTC).isoformat(),
        "verified": not mismatches,
        "mismatches": mismatches,
        "baseline_manifest_digest": baseline.get("manifest_digest"),
        "current_manifest_digest": current.get("manifest_digest"),
        "current_summary": {
            "total_rows": current["database"]["total_rows"],
            "originals_verified": current["originals"]["verified"],
            "bytes_verified": current["originals"]["bytes_verified"],
            "schema_versions": current["database"]["schema_versions"],
        },
    }


def _load_baseline(path: Path, manifest_key: str) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.stat().st_size > MAX_BASELINE_BYTES:
        raise DurabilityError("Baseline file is missing or exceeds the safe size limit")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DurabilityError("Baseline file is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DurabilityError("Baseline must be a JSON object")
    core = {
        key: value.get(key)
        for key in (
            "report_version",
            "fingerprint_algorithm",
            "durable_target",
            "database",
            "originals",
            "controls",
            "manifest_key_fingerprint",
        )
    }
    key = manifest_key.encode("utf-8")
    if len(key) < 32:
        raise DurabilityError("Durability manifest key must contain at least 32 characters")
    if core["manifest_key_fingerprint"] != hashlib.sha256(key).hexdigest()[:16]:
        raise DurabilityError("Baseline was signed with a different durability manifest key")
    if not hmac.compare_digest(
        str(value.get("manifest_digest", "")), _manifest_digest(core, key)
    ):
        raise DurabilityError("Baseline manifest digest is invalid")
    return value


def _write_report(path: Path, report: dict[str, Any]) -> None:
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_suffix(resolved.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(resolved)


def run(
    *,
    mode: str,
    settings: Settings,
    output: Path,
    baseline_path: Path | None = None,
    allow_local: bool = False,
    max_rows: int = 1_000_000,
    max_originals: int = 10_000,
    max_bytes: int = 10_000_000_000,
) -> dict[str, Any]:
    if mode == "verify" and baseline_path is None:
        raise DurabilityError("Verify mode requires --baseline")
    if min(max_rows, max_originals, max_bytes) < 1:
        raise DurabilityError("Acceptance limits must be positive integers")
    durable_target = _validate_target(settings, allow_local)
    manifest_key = (
        settings.durability_manifest_key.get_secret_value()
        if settings.durability_manifest_key
        else "local-durability-rehearsal-key-not-for-managed-acceptance"
        if allow_local
        else None
    )
    if manifest_key is None:
        raise DurabilityError(
            "EVIDENCEDESK_DURABILITY_MANIFEST_KEY is required for managed restore acceptance"
        )
    database = build_database(settings)
    storage = build_document_storage(settings)
    try:
        database.open()
        current = create_manifest(
            database,
            storage,
            durable_target=durable_target,
            max_rows=max_rows,
            max_originals=max_originals,
            max_bytes=max_bytes,
            manifest_key=manifest_key,
        )
        if mode == "snapshot":
            report = current
        elif mode == "verify" and baseline_path is not None:
            report = compare_manifest(_load_baseline(baseline_path, manifest_key), current)
        else:
            raise DurabilityError("Verify mode requires --baseline")
        _write_report(output, report)
        return report
    finally:
        database.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture and verify secret-free EvidenceDesk backup/restore manifests."
    )
    parser.add_argument("mode", choices=("snapshot", "verify"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--allow-local", action="store_true")
    parser.add_argument("--max-rows", type=int, default=1_000_000)
    parser.add_argument("--max-originals", type=int, default=10_000)
    parser.add_argument("--max-bytes", type=int, default=10_000_000_000)
    args = parser.parse_args()
    try:
        report = run(
            mode=args.mode,
            settings=Settings(),
            output=args.output,
            baseline_path=args.baseline,
            allow_local=args.allow_local,
            max_rows=args.max_rows,
            max_originals=args.max_originals,
            max_bytes=args.max_bytes,
        )
    except (DurabilityError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}))
        return 2
    print(
        json.dumps(
            {
                "status": (
                    "verified"
                    if report.get("verified") is True
                    else "mismatch"
                    if report.get("verified") is False
                    else "captured"
                ),
                "report": str(args.output.resolve()),
            }
        )
    )
    return 0 if report.get("verified", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
