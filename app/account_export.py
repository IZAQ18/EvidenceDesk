from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from app.accounts import AccountSession
from app.db import DatabaseLike
from app.storage import OriginalStorage


class ExportTooLarge(ValueError):
    pass


def _rows(connection: Any, query: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def _safe_filename(value: str) -> str:
    name = Path(value).name
    safe = "".join(character for character in name if character.isalnum() or character in "-_. ")
    return safe[:120] or "document.bin"


def build_workspace_export(
    database: DatabaseLike,
    storage: OriginalStorage,
    session: AccountSession,
    max_bytes: int,
) -> bytes:
    """Build an administrator-authorized archive without credentials or token hashes."""
    if session.role != "admin":
        raise PermissionError("Administrator access is required for a complete workspace export")
    tenant_id = session.workspace_id
    with database.connect() as connection:
        account = dict(
            connection.execute(
                """SELECT id, email, display_name, status, email_verified_at,
                          created_at, updated_at FROM users WHERE id = ?""",
                (session.user_id,),
            ).fetchone()
        )
        workspace = dict(
            connection.execute(
                "SELECT id, name, plan, created_by, created_at FROM workspaces WHERE id = ?",
                (tenant_id,),
            ).fetchone()
        )
        members = _rows(
            connection,
            """SELECT u.id, u.email, u.display_name, u.status, u.email_verified_at,
                      m.role, m.clearances_json, m.created_at
               FROM workspace_members m JOIN users u ON u.id = m.user_id
               WHERE m.workspace_id = ? ORDER BY m.created_at""",
            (tenant_id,),
        )
        invitations = _rows(
            connection,
            """SELECT id, email, role, clearances_json, invited_by, status,
                      created_at, expires_at, accepted_at
               FROM workspace_invitations WHERE workspace_id = ? ORDER BY created_at""",
            (tenant_id,),
        )
        documents = _rows(
            connection,
            "SELECT * FROM documents WHERE tenant_id = ? ORDER BY created_at",
            (tenant_id,),
        )
        original_references = [
            (document["id"], document["filename"], document.get("storage_path"))
            for document in documents
        ]
        document_ids = [document["id"] for document in documents]
        pages: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        for document_id in document_ids:
            pages.extend(
                _rows(
                    connection,
                    "SELECT * FROM document_pages WHERE document_id = ? ORDER BY page_number",
                    (document_id,),
                )
            )
            chunks.extend(
                _rows(
                    connection,
                    "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index",
                    (document_id,),
                )
            )
        conversations = _rows(
            connection,
            "SELECT * FROM conversations WHERE tenant_id = ? ORDER BY created_at",
            (tenant_id,),
        )
        messages: list[dict[str, Any]] = []
        for conversation in conversations:
            messages.extend(
                _rows(
                    connection,
                    "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                    (conversation["id"],),
                )
            )
        payload = {
            "exported_at": datetime.now(UTC).isoformat(),
            "scope": "complete_current_workspace",
            "account": account,
            "workspace": workspace,
            "members": members,
            "invitations": invitations,
            "documents": [dict(document) for document in documents],
            "document_pages": pages,
            "chunks": chunks,
            "conversations": conversations,
            "messages": messages,
            "feedback": _rows(
                connection, "SELECT * FROM feedback WHERE tenant_id = ?", (tenant_id,)
            ),
            "pilot_reviews": _rows(
                connection, "SELECT * FROM pilot_reviews WHERE tenant_id = ?", (tenant_id,)
            ),
            "audit_events": _rows(
                connection, "SELECT * FROM audit_events WHERE tenant_id = ?", (tenant_id,)
            ),
            "ingestion_jobs": _rows(
                connection, "SELECT * FROM ingestion_jobs WHERE tenant_id = ?", (tenant_id,)
            ),
            "answer_diagnostics": _rows(
                connection,
                "SELECT * FROM answer_diagnostics WHERE tenant_id = ?",
                (tenant_id,),
            ),
            "model_usage_daily": _rows(
                connection, "SELECT * FROM model_usage_daily WHERE tenant_id = ?", (tenant_id,)
            ),
            "model_usage_events": _rows(
                connection, "SELECT * FROM model_usage_events WHERE tenant_id = ?", (tenant_id,)
            ),
            "payment_events": _rows(
                connection, "SELECT * FROM payment_events WHERE tenant_id = ?", (tenant_id,)
            ),
            "missing_originals": [],
        }
    for document in payload["documents"]:
        document["has_original"] = bool(document.pop("storage_path", None))
    for job in payload["ingestion_jobs"]:
        job["has_staged_original"] = bool(job.pop("storage_path", None))

    buffer = io.BytesIO()
    raw_bytes = 0
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for document_id, original_filename, reference in original_references:
            if not reference:
                continue
            filename = _safe_filename(str(original_filename))
            archive_name = f"originals/{document_id}-{filename}"
            try:
                with archive.open(archive_name, "w") as target:
                    for chunk in storage.stream(str(reference)):
                        raw_bytes += len(chunk)
                        if raw_bytes > max_bytes:
                            raise ExportTooLarge(
                                "Workspace export exceeds the configured size limit"
                            )
                        target.write(chunk)
            except FileNotFoundError:
                payload["missing_originals"].append(str(document_id))
        manifest = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        raw_bytes += len(manifest)
        if raw_bytes > max_bytes:
            raise ExportTooLarge("Workspace export exceeds the configured size limit")
        archive.writestr("manifest.json", manifest)
    return buffer.getvalue()
