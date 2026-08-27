from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config import Classification
from app.db import DatabaseLike
from app.ingestion import ExtractedDocument, TextChunk, has_injection_language
from app.models import Principal, RetrievalSummary
from app.payments import EntitlementDecision, PaymentEvent
from app.plans import PlanPolicy, allowance
from app.retrieval import (
    STOP_WORDS,
    expanded_query_terms,
    passage_relevance,
    semantic_similarity,
    terms,
)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    document_id: str
    filename: str
    source: str | None
    classification: Classification
    text: str
    score: float
    relevance_score: float
    injection_flag: bool
    page_number: int | None
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    rerank_score: float = 0.0


@dataclass(frozen=True)
class SearchDiagnostics:
    mode: str
    candidate_pool: int
    lexical_hits: int
    semantic_hits: int
    returned_chunks: int
    best_semantic_score: float
    best_rerank_score: float


@dataclass(frozen=True)
class HybridSearchResult:
    chunks: list[RetrievedChunk]
    diagnostics: SearchDiagnostics


class DuplicateDocumentError(Exception):
    pass


class PlanLimitExceeded(Exception):
    def __init__(self, resource: str, limit: int):
        self.resource = resource
        self.limit = limit
        super().__init__(f"{resource} allowance reached")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    def __init__(
        self,
        database: DatabaseLike,
        *,
        semantic_candidate_limit: int = 600,
        semantic_min_score: float = 0.08,
    ):
        self.database = database
        self.semantic_candidate_limit = semantic_candidate_limit
        self.semantic_min_score = semantic_min_score

    def _lock_workspace(self, connection, workspace_id: str) -> None:
        if self.database.dialect == "postgres":
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (workspace_id,)
            )
        else:
            connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _document_slots(connection, workspace_id: str) -> int:
        row = connection.execute(
            """SELECT COUNT(*) count FROM (
                   SELECT id slot_id FROM documents WHERE tenant_id = ?
                   UNION
                   SELECT document_id slot_id FROM ingestion_jobs
                   WHERE tenant_id = ? AND status IN ('queued','processing')
               ) slots""",
            (workspace_id, workspace_id),
        ).fetchone()
        return int(row["count"])

    def workspace_plan(self, workspace_id: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT plan FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        return str(row["plan"]) if row else None

    def reserve_answer_allowance(self, workspace_id: str, limit: int | None) -> bool:
        if limit is None:
            return True
        usage_month = datetime.now(UTC).strftime("%Y-%m")
        now = _now()
        with self.database.connect() as connection:
            insert = (
                """INSERT INTO workspace_usage_monthly(
                       workspace_id, usage_month, answer_count, updated_at)
                   VALUES (?, ?, 0, ?)
                   ON CONFLICT(workspace_id, usage_month) DO NOTHING"""
                if self.database.dialect == "postgres"
                else """INSERT OR IGNORE INTO workspace_usage_monthly(
                       workspace_id, usage_month, answer_count, updated_at)
                   VALUES (?, ?, 0, ?)"""
            )
            connection.execute(insert, (workspace_id, usage_month, now))
            cursor = connection.execute(
                """UPDATE workspace_usage_monthly
                   SET answer_count = answer_count + 1, updated_at = ?
                   WHERE workspace_id = ? AND usage_month = ? AND answer_count < ?""",
                (now, workspace_id, usage_month, limit),
            )
            return cursor.rowcount == 1

    def workspace_usage(self, principal: Principal, policy: PlanPolicy) -> dict:
        usage_month = datetime.now(UTC).strftime("%Y-%m")
        usage_date = datetime.now(UTC).date().isoformat()
        with self.database.connect() as connection:
            documents = connection.execute(
                """SELECT COUNT(*) count, COALESCE(SUM(size_bytes), 0) storage_bytes
                   FROM documents WHERE tenant_id = ?""",
                (principal.tenant_id,),
            ).fetchone()
            pending = connection.execute(
                """SELECT COUNT(*) count FROM ingestion_jobs
                   WHERE tenant_id = ? AND status IN ('queued','processing')""",
                (principal.tenant_id,),
            ).fetchone()
            monthly = connection.execute(
                """SELECT answer_count, updated_at FROM workspace_usage_monthly
                   WHERE workspace_id = ? AND usage_month = ?""",
                (principal.tenant_id, usage_month),
            ).fetchone()
            model_today = connection.execute(
                """SELECT request_count, reserved_tokens, reserved_cost_microusd
                   FROM model_usage_daily WHERE tenant_id = ? AND usage_date = ?""",
                (principal.tenant_id, usage_date),
            ).fetchone()
            # A processing job can already have created its document row. Count the
            # shared document id once so the usage meter matches the enforced limit.
            document_slots = self._document_slots(connection, principal.tenant_id)
        answer_count = int(monthly["answer_count"]) if monthly else 0
        return {
            "managed": policy.managed,
            "plan": policy.key,
            "plan_name": policy.name,
            "period": usage_month,
            "documents": allowance(document_slots, policy.document_limit),
            "stored_documents": int(documents["count"]),
            "pending_documents": int(pending["count"]),
            "storage_bytes": int(documents["storage_bytes"]),
            "answers": allowance(answer_count, policy.monthly_answer_limit),
            "model_today": (
                dict(model_today)
                if model_today
                else {
                    "request_count": 0,
                    "reserved_tokens": 0,
                    "reserved_cost_microusd": 0,
                }
            ),
            "updated_at": monthly["updated_at"] if monthly else None,
        }

    def audit(
        self,
        principal: Principal,
        event_type: str,
        entity_type: str,
        entity_id: str | None,
        detail: dict | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO audit_events
                   (tenant_id, subject, event_type, entity_type, entity_id, detail_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    principal.tenant_id,
                    principal.subject,
                    event_type,
                    entity_type,
                    entity_id,
                    json.dumps(detail or {}, separators=(",", ":")),
                    _now(),
                ),
            )

    def create_ingestion_job(
        self,
        *,
        job_id: str,
        document_id: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        classification: Classification,
        source: str | None,
        collection: str | None,
        version: str | None,
        effective_date: str | None,
        expires_at: str | None,
        supersedes_document_id: str | None,
        storage_path: str,
        principal: Principal,
        max_attempts: int,
        document_limit: int | None = None,
    ) -> dict:
        now = _now()
        with self.database.connect() as connection:
            if document_limit is not None:
                self._lock_workspace(connection, principal.tenant_id)
                if self._document_slots(connection, principal.tenant_id) >= document_limit:
                    raise PlanLimitExceeded("document", document_limit)
            connection.execute(
                """INSERT INTO ingestion_jobs
                   (id, document_id, tenant_id, owner_subject, owner_role,
                    owner_clearances_json, filename, content_type, size_bytes, sha256,
                    classification, source, collection, version, effective_date, expires_at,
                    supersedes_document_id, storage_path, status, stage, progress, detail,
                    attempts, max_attempts, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'queued', 'queued', 15, ?, 0, ?, ?, ?)""",
                (
                    job_id,
                    document_id,
                    principal.tenant_id,
                    principal.subject,
                    principal.role,
                    json.dumps(principal.clearances, separators=(",", ":")),
                    filename,
                    content_type,
                    size_bytes,
                    sha256,
                    classification,
                    source,
                    collection,
                    version,
                    effective_date,
                    expires_at,
                    supersedes_document_id,
                    storage_path,
                    "Upload isolated in quarantine. Waiting for security scanning.",
                    max_attempts,
                    now,
                    now,
                ),
            )
        return self.get_ingestion_job(job_id, principal) or {}

    def claim_ingestion_job(self, worker_id: str, lease_seconds: int) -> dict | None:
        now = _now()
        lease = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        for _ in range(5):
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id FROM ingestion_jobs
                       WHERE attempts < max_attempts
                         AND (status = 'queued' OR
                              (status = 'processing' AND lease_expires_at < ?))
                       ORDER BY created_at LIMIT 1""",
                    (now,),
                ).fetchone()
                if not row:
                    return None
                cursor = connection.execute(
                    """UPDATE ingestion_jobs
                       SET status = 'processing', stage = 'security_check', progress = 20,
                           detail = ?, attempts = attempts + 1, worker_id = ?,
                           lease_expires_at = ?, updated_at = ?, error_code = NULL,
                           error_message = NULL, error_action = NULL
                       WHERE id = ? AND attempts < max_attempts
                         AND (status = 'queued' OR
                              (status = 'processing' AND lease_expires_at < ?))""",
                    (
                        "Rechecking the staged file before extraction.",
                        worker_id,
                        lease,
                        now,
                        row["id"],
                        now,
                    ),
                )
                if cursor.rowcount:
                    claimed = connection.execute(
                        "SELECT * FROM ingestion_jobs WHERE id = ?", (row["id"],)
                    ).fetchone()
                    return dict(claimed)
        return None

    def update_ingestion_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        stage: str,
        progress: int,
        detail: str,
        lease_seconds: int,
    ) -> bool:
        now = _now()
        lease = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """UPDATE ingestion_jobs SET stage = ?, progress = ?, detail = ?,
                          lease_expires_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'processing' AND worker_id = ?""",
                (stage, progress, detail, lease, now, job_id, worker_id),
            )
            return cursor.rowcount > 0

    def finish_ingestion_job(
        self, job_id: str, worker_id: str, storage_path: str | None = None
    ) -> bool:
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """UPDATE ingestion_jobs
                   SET status = 'ready', stage = 'ready', progress = 100,
                       detail = 'Evidence is indexed and ready to search.',
                       storage_path = COALESCE(?, storage_path),
                       lease_expires_at = NULL, worker_id = NULL, updated_at = ?, finished_at = ?
                   WHERE id = ? AND status = 'processing' AND worker_id = ?""",
                (storage_path, now, now, job_id, worker_id),
            )
            return cursor.rowcount > 0

    def fail_ingestion_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        code: str,
        message: str,
        action: str | None,
        retryable: bool = True,
        quarantined: bool = False,
    ) -> bool:
        now = _now()
        with self.database.connect() as connection:
            job = connection.execute(
                """SELECT attempts, max_attempts FROM ingestion_jobs
                   WHERE id = ? AND status = 'processing' AND worker_id = ?""",
                (job_id, worker_id),
            ).fetchone()
            if not job:
                return False
            if quarantined:
                stage = "quarantined"
            elif not retryable or job["attempts"] >= job["max_attempts"]:
                stage = "dead_letter"
            else:
                stage = "failed"
            cursor = connection.execute(
                """UPDATE ingestion_jobs
                   SET status = 'failed', stage = ?, progress = 100, detail = ?,
                       error_code = ?, error_message = ?, error_action = ?,
                       lease_expires_at = NULL, worker_id = NULL, updated_at = ?, finished_at = ?
                   WHERE id = ? AND status = 'processing' AND worker_id = ?""",
                (stage, message, code, message, action, now, now, job_id, worker_id),
            )
            return cursor.rowcount > 0

    def list_ingestion_jobs(self, principal: Principal, limit: int = 20) -> list[dict]:
        placeholders = ",".join("?" for _ in principal.clearances)
        query = f"""SELECT * FROM ingestion_jobs WHERE tenant_id = ?
                    AND classification IN ({placeholders})
                    ORDER BY created_at DESC LIMIT ?"""  # noqa: S608
        with self.database.connect() as connection:
            rows = connection.execute(
                query, (principal.tenant_id, *principal.clearances, limit)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_ingestion_job(self, job_id: str, principal: Principal) -> dict | None:
        placeholders = ",".join("?" for _ in principal.clearances)
        query = f"""SELECT * FROM ingestion_jobs WHERE id = ? AND tenant_id = ?
                    AND classification IN ({placeholders})"""  # noqa: S608
        with self.database.connect() as connection:
            row = connection.execute(
                query, (job_id, principal.tenant_id, *principal.clearances)
            ).fetchone()
        return dict(row) if row else None

    def retry_ingestion_job(self, job_id: str, principal: Principal) -> bool:
        placeholders = ",".join("?" for _ in principal.clearances)
        query = f"""UPDATE ingestion_jobs
                    SET status = 'queued', stage = 'queued', progress = 15,
                        detail = 'Retry requested. Waiting for an ingestion worker.',
                        error_code = NULL, error_message = NULL, error_action = NULL,
                        worker_id = NULL, lease_expires_at = NULL, finished_at = NULL,
                        updated_at = ?
                    WHERE id = ? AND tenant_id = ? AND status = 'failed' AND stage = 'failed'
                      AND attempts < max_attempts
                      AND classification IN ({placeholders})"""  # noqa: S608
        with self.database.connect() as connection:
            cursor = connection.execute(
                query, (_now(), job_id, principal.tenant_id, *principal.clearances)
            )
            return cursor.rowcount > 0

    def delete_ingestion_job(
        self, job_id: str, principal: Principal
    ) -> tuple[bool, str | None, str | None]:
        job = self.get_ingestion_job(job_id, principal)
        if not job or job["status"] == "processing":
            return False, None, None
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM ingestion_jobs WHERE id = ? AND tenant_id = ?",
                (job_id, principal.tenant_id),
            )
        staged_path = job.get("storage_path") if job["status"] != "ready" else None
        return True, staged_path, job.get("document_id")

    def add_document(
        self,
        extracted: ExtractedDocument,
        chunks: list[TextChunk],
        classification: Classification,
        source: str | None,
        principal: Principal,
        *,
        document_id: str | None = None,
        storage_path: str | None = None,
        collection: str | None = None,
        version: str | None = None,
        effective_date: str | None = None,
        expires_at: str | None = None,
        supersedes_document_id: str | None = None,
        document_limit: int | None = None,
    ) -> str:
        document_id = document_id or str(uuid4())
        now = _now()
        try:
            with self.database.connect() as connection:
                if document_limit is not None:
                    self._lock_workspace(connection, principal.tenant_id)
                    if self._document_slots(connection, principal.tenant_id) >= document_limit:
                        raise PlanLimitExceeded("document", document_limit)
                connection.execute(
                    """INSERT INTO documents
                       (id, tenant_id, filename, content_type, size_bytes, sha256, source,
                        classification, created_at, created_by, page_count, word_count,
                        extraction_method, extraction_warning, collection, version,
                        effective_date, expires_at, status, storage_path)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
                    (
                        document_id,
                        principal.tenant_id,
                        extracted.filename,
                        extracted.content_type,
                        len(extracted.content),
                        extracted.sha256,
                        source,
                        classification,
                        now,
                        principal.subject,
                        extracted.page_count,
                        extracted.word_count,
                        extracted.extraction_method,
                        extracted.extraction_warning,
                        collection,
                        version,
                        effective_date,
                        expires_at,
                        storage_path,
                    ),
                )
                for page in extracted.pages:
                    connection.execute(
                        """INSERT INTO document_pages
                           (document_id, page_number, text, ocr_used) VALUES (?, ?, ?, ?)""",
                        (document_id, page.page_number, page.text, int(page.ocr_used)),
                    )
                for index, chunk in enumerate(chunks):
                    connection.execute(
                        """INSERT INTO chunks
                           (document_id, tenant_id, chunk_index, text, classification,
                            injection_flag, page_number)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            document_id,
                            principal.tenant_id,
                            index,
                            chunk.text,
                            classification,
                            int(has_injection_language(chunk.text)),
                            chunk.page_number,
                        ),
                    )
                    if self.database.dialect == "sqlite":
                        chunk_id = connection.execute(
                            "SELECT last_insert_rowid() AS id"
                        ).fetchone()["id"]
                        connection.execute(
                            "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
                            (chunk_id, chunk.text),
                        )
                if supersedes_document_id:
                    connection.execute(
                        """UPDATE documents SET status = 'superseded'
                           WHERE id = ? AND tenant_id = ? AND status = 'active'""",
                        (supersedes_document_id, principal.tenant_id),
                    )
        except Exception as exc:
            constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
            if (
                "documents.tenant_id, documents.sha256" in str(exc)
                or constraint == "idx_documents_dedupe"
            ):
                raise DuplicateDocumentError from exc
            raise
        return document_id

    def list_documents(self, principal: Principal) -> list[dict]:
        placeholders = ",".join("?" for _ in principal.clearances)
        query = f"""SELECT d.*, COUNT(c.id) AS chunk_count,
                           CASE WHEN d.storage_path IS NULL THEN 0 ELSE 1 END AS has_original
                    FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
                    WHERE d.tenant_id = ? AND d.classification IN ({placeholders})
                    GROUP BY d.id ORDER BY d.created_at DESC"""  # noqa: S608
        with self.database.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    query, (principal.tenant_id, *principal.clearances)
                ).fetchall()
            ]

    def get_document(self, document_id: str, principal: Principal) -> dict | None:
        placeholders = ",".join("?" for _ in principal.clearances)
        query = f"""SELECT d.*, CASE WHEN d.storage_path IS NULL THEN 0 ELSE 1 END AS has_original
                    FROM documents d WHERE d.id = ? AND d.tenant_id = ?
                    AND d.classification IN ({placeholders})"""  # noqa: S608
        with self.database.connect() as connection:
            row = connection.execute(
                query, (document_id, principal.tenant_id, *principal.clearances)
            ).fetchone()
        return dict(row) if row else None

    def get_page(self, document_id: str, page_number: int, principal: Principal) -> dict | None:
        placeholders = ",".join("?" for _ in principal.clearances)
        query = f"""SELECT p.page_number, p.text, p.ocr_used, d.filename, d.classification
                    FROM document_pages p JOIN documents d ON d.id = p.document_id
                    WHERE d.id = ? AND p.page_number = ? AND d.tenant_id = ?
                    AND d.classification IN ({placeholders})"""  # noqa: S608
        with self.database.connect() as connection:
            row = connection.execute(
                query,
                (document_id, page_number, principal.tenant_id, *principal.clearances),
            ).fetchone()
        return dict(row) if row else None

    def set_document_status(
        self, document_id: str, status: str, principal: Principal
    ) -> bool:
        placeholders = ",".join("?" for _ in principal.clearances)
        query = f"""UPDATE documents SET status = ? WHERE id = ? AND tenant_id = ?
                    AND classification IN ({placeholders})"""  # noqa: S608
        with self.database.connect() as connection:
            cursor = connection.execute(
                query, (status, document_id, principal.tenant_id, *principal.clearances)
            )
            return cursor.rowcount > 0

    def delete_document(self, document_id: str, principal: Principal) -> tuple[bool, str | None]:
        document = self.get_document(document_id, principal)
        if not document:
            return False, None
        with self.database.connect() as connection:
            if self.database.dialect == "sqlite":
                chunk_ids = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM chunks WHERE document_id = ?", (document_id,)
                    )
                ]
                for chunk_id in chunk_ids:
                    connection.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return True, document.get("storage_path")

    def search(
        self,
        question: str,
        principal: Principal,
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        return self.hybrid_search(question, principal, top_k, document_ids).chunks

    def hybrid_search(
        self,
        question: str,
        principal: Principal,
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> HybridSearchResult:
        exact_terms, expanded_set = expanded_query_terms(question)
        empty_diagnostics = SearchDiagnostics(
            mode="authorized-keyword-plus-local-semantic",
            candidate_pool=0,
            lexical_hits=0,
            semantic_hits=0,
            returned_chunks=0,
            best_semantic_score=0.0,
            best_rerank_score=0.0,
        )
        if not exact_terms:
            return HybridSearchResult([], empty_diagnostics)
        placeholders = ",".join("?" for _ in principal.clearances)
        scope_clause = ""
        scope_parameters: tuple[str, ...] = ()
        if document_ids:
            scope_placeholders = ",".join("?" for _ in document_ids)
            scope_clause = f"AND c.document_id IN ({scope_placeholders})"
            scope_parameters = tuple(document_ids)
        candidate_limit = max(top_k * 6, 24)
        raw_query_terms = {
            word
            for word in re.findall(r"[a-z0-9][a-z0-9'-]+", question.lower())
            if word not in STOP_WORDS
        }
        lexical_terms = list(
            dict.fromkeys(
                [
                    *sorted(raw_query_terms),
                    *sorted(exact_terms),
                    *sorted(expanded_set - exact_terms),
                ]
            )
        )[:30]
        fts_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in lexical_terms)
        if self.database.dialect == "postgres":
            query = f"""SELECT c.id AS chunk_id, c.document_id, d.filename, d.source,
                               c.classification, c.text, c.injection_flag, c.page_number,
                               ts_rank_cd(
                                   to_tsvector('simple', c.text),
                                   websearch_to_tsquery('simple', ?)
                               ) AS score
                        FROM chunks c JOIN documents d ON d.id = c.document_id
                        WHERE to_tsvector('simple', c.text)
                              @@ websearch_to_tsquery('simple', ?)
                          AND c.tenant_id = ?
                          AND c.classification IN ({placeholders})
                          AND d.status = 'active' {scope_clause}
                        ORDER BY score DESC LIMIT ?"""  # noqa: S608
            parameters = (
                fts_query,
                fts_query,
                principal.tenant_id,
                *principal.clearances,
                *scope_parameters,
                candidate_limit,
            )
        else:
            query = f"""SELECT c.id AS chunk_id, c.document_id, d.filename, d.source,
                               c.classification, c.text, c.injection_flag, c.page_number,
                               bm25(chunks_fts) AS score
                        FROM chunks_fts
                        JOIN chunks c ON c.id = chunks_fts.rowid
                        JOIN documents d ON d.id = c.document_id
                        WHERE chunks_fts MATCH ? AND c.tenant_id = ?
                          AND c.classification IN ({placeholders}) AND d.status = 'active'
                          {scope_clause}
                        ORDER BY score ASC LIMIT ?"""  # noqa: S608
            parameters = (
                fts_query,
                principal.tenant_id,
                *principal.clearances,
                *scope_parameters,
                candidate_limit,
            )
        semantic_query = f"""SELECT c.id AS chunk_id, c.document_id, d.filename, d.source,
                                    c.classification, c.text, c.injection_flag, c.page_number,
                                    0.0 AS score
                             FROM chunks c JOIN documents d ON d.id = c.document_id
                             WHERE c.tenant_id = ?
                               AND c.classification IN ({placeholders})
                               AND d.status = 'active' {scope_clause}
                             ORDER BY d.created_at DESC, c.chunk_index ASC LIMIT ?"""  # noqa: S608
        semantic_parameters = (
            principal.tenant_id,
            *principal.clearances,
            *scope_parameters,
            self.semantic_candidate_limit,
        )
        with self.database.connect() as connection:
            lexical_rows = connection.execute(query, parameters).fetchall()
            semantic_rows = connection.execute(
                semantic_query, semantic_parameters
            ).fetchall()

        lexical_rank = {row["chunk_id"]: rank for rank, row in enumerate(lexical_rows)}
        semantic_scored = sorted(
            (
                (semantic_similarity(question, row["text"]), row)
                for row in semantic_rows
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        semantic_rank = {
            row["chunk_id"]: rank
            for rank, (score, row) in enumerate(semantic_scored)
            if score >= self.semantic_min_score
        }
        combined: dict[int, dict] = {row["chunk_id"]: dict(row) for row in lexical_rows}
        for score, row in semantic_scored:
            row_terms = terms(row["text"])
            if score < self.semantic_min_score and not (exact_terms & row_terms):
                continue
            combined.setdefault(row["chunk_id"], dict(row))

        ranked: list[RetrievedChunk] = []
        for chunk_id, row_data in combined.items():
            text_terms = terms(row_data["text"])
            exact_overlap = len(exact_terms & text_terms)
            expanded_overlap = len((expanded_set - exact_terms) & text_terms)
            metadata = f"{row_data['filename']} {row_data['source'] or ''}"
            metadata_overlap = len(exact_terms & terms(metadata))
            phrase_boost = 0.35 if question.lower().strip() in row_data["text"].lower() else 0.0
            semantic_score = semantic_similarity(question, row_data["text"])
            rerank = passage_relevance(question, row_data["text"], metadata)
            lexical_fusion = (
                0.9 / (lexical_rank[chunk_id] + 1) if chunk_id in lexical_rank else 0.0
            )
            semantic_fusion = (
                0.7 / (semantic_rank[chunk_id] + 1) if chunk_id in semantic_rank else 0.0
            )
            lexical_score = exact_overlap + expanded_overlap * 0.35 + metadata_overlap * 0.25
            relevance = (
                rerank["score"]
                + lexical_fusion
                + semantic_fusion
                + phrase_boost
            )
            row_data["injection_flag"] = bool(row_data["injection_flag"])
            ranked.append(
                RetrievedChunk(
                    **row_data,
                    relevance_score=round(relevance, 4),
                    lexical_score=round(lexical_score, 4),
                    semantic_score=round(semantic_score, 4),
                    rerank_score=round(rerank["score"], 4),
                )
            )
        ranked.sort(key=lambda item: item.relevance_score, reverse=True)
        selected: list[RetrievedChunk] = []
        seen_text: set[str] = set()
        page_counts: Counter[tuple[str, int | None]] = Counter()
        for chunk in ranked:
            normalized = re.sub(r"\W+", " ", chunk.text.lower()).strip()
            page_key = (chunk.document_id, chunk.page_number)
            if not normalized or normalized in seen_text or page_counts[page_key] >= 3:
                continue
            selected.append(chunk)
            seen_text.add(normalized)
            page_counts[page_key] += 1
            if len(selected) == top_k:
                break
        diagnostics = SearchDiagnostics(
            mode="authorized-keyword-plus-local-semantic",
            candidate_pool=len(semantic_rows),
            lexical_hits=len(lexical_rows),
            semantic_hits=len(semantic_rank),
            returned_chunks=len(selected),
            best_semantic_score=max((item.semantic_score for item in selected), default=0.0),
            best_rerank_score=max((item.rerank_score for item in selected), default=0.0),
        )
        return HybridSearchResult(selected, diagnostics)

    def create_or_get_conversation(
        self, principal: Principal, question: str, conversation_id: str | None
    ) -> str:
        now = _now()
        with self.database.connect() as connection:
            if conversation_id:
                row = connection.execute(
                    """SELECT id FROM conversations
                       WHERE id = ? AND tenant_id = ? AND owner_subject = ?""",
                    (conversation_id, principal.tenant_id, principal.subject),
                ).fetchone()
                if not row:
                    raise ValueError("Conversation not found")
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
                )
                return conversation_id
            new_id = str(uuid4())
            title = question.strip().replace("\n", " ")[:72]
            connection.execute(
                """INSERT INTO conversations
                   (id, tenant_id, owner_subject, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_id, principal.tenant_id, principal.subject, title, now, now),
            )
            return new_id

    def record_exchange(
        self,
        principal: Principal,
        conversation_id: str,
        question: str,
        answer: str,
        grounded: bool,
        evidence_quality: str,
        citations: list[dict],
        workflow: list[dict],
    ) -> str:
        now = _now()
        assistant_id = str(uuid4())
        with self.database.connect() as connection:
            conversation = connection.execute(
                """SELECT id FROM conversations
                   WHERE id = ? AND tenant_id = ? AND owner_subject = ?""",
                (conversation_id, principal.tenant_id, principal.subject),
            ).fetchone()
            if not conversation:
                raise ValueError("Conversation not found")
            connection.execute(
                """INSERT INTO messages
                   (id, conversation_id, role, content, created_at)
                   VALUES (?, ?, 'user', ?, ?)""",
                (str(uuid4()), conversation_id, question, now),
            )
            connection.execute(
                """INSERT INTO messages
                   (id, conversation_id, role, content, grounded, evidence_quality,
                    citations_json, workflow_json, created_at)
                   VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?)""",
                (
                    assistant_id,
                    conversation_id,
                    answer,
                    int(grounded),
                    evidence_quality,
                    json.dumps(citations, separators=(",", ":")),
                    json.dumps(workflow, separators=(",", ":")),
                    now,
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
            )
        return assistant_id

    def list_conversations(self, principal: Principal) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT c.*, COUNT(m.id) AS message_count
                   FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.tenant_id = ? AND c.owner_subject = ?
                   GROUP BY c.id ORDER BY c.updated_at DESC LIMIT 100""",
                (principal.tenant_id, principal.subject),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_messages(self, conversation_id: str, principal: Principal) -> list[dict] | None:
        with self.database.connect() as connection:
            conversation = connection.execute(
                """SELECT id FROM conversations
                   WHERE id = ? AND tenant_id = ? AND owner_subject = ?""",
                (conversation_id, principal.tenant_id, principal.subject),
            ).fetchone()
            if not conversation:
                return None
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["grounded"] = bool(item["grounded"]) if item["grounded"] is not None else None
            item["citations"] = json.loads(item.pop("citations_json"))
            item.pop("workflow_json")
            item.pop("conversation_id")
            result.append(item)
        return result

    def delete_conversation(self, conversation_id: str, principal: Principal) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """DELETE FROM conversations
                   WHERE id = ? AND tenant_id = ? AND owner_subject = ?""",
                (conversation_id, principal.tenant_id, principal.subject),
            )
            return cursor.rowcount > 0

    def add_feedback(
        self,
        principal: Principal,
        message_id: str,
        category: str,
        comment: str | None,
    ) -> bool:
        with self.database.connect() as connection:
            authorized = connection.execute(
                """SELECT m.id FROM messages m
                   JOIN conversations c ON c.id = m.conversation_id
                   WHERE m.id = ? AND m.role = 'assistant'
                     AND c.tenant_id = ? AND c.owner_subject = ?""",
                (message_id, principal.tenant_id, principal.subject),
            ).fetchone()
            if not authorized:
                return False
            connection.execute(
                """INSERT INTO feedback
                   (id, tenant_id, subject, message_id, category, comment, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(subject, message_id) DO UPDATE SET
                     category=excluded.category, comment=excluded.comment,
                     created_at=excluded.created_at""",
                (
                    str(uuid4()),
                    principal.tenant_id,
                    principal.subject,
                    message_id,
                    category,
                    comment,
                    _now(),
                ),
            )
            return True

    def add_pilot_review(
        self,
        principal: Principal,
        *,
        reviewer_context: str,
        ease_score: int,
        trust_score: int,
        usefulness_score: int,
        completed_tasks: int,
        would_return: str,
        most_valuable: str | None,
        improvement: str | None,
    ) -> str:
        review_id = str(uuid4())
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO pilot_reviews
                   (id, tenant_id, subject, reviewer_context, ease_score, trust_score,
                    usefulness_score, completed_tasks, would_return, most_valuable,
                    improvement, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id,
                    principal.tenant_id,
                    principal.subject,
                    reviewer_context,
                    ease_score,
                    trust_score,
                    usefulness_score,
                    completed_tasks,
                    would_return,
                    most_valuable,
                    improvement,
                    _now(),
                ),
            )
        return review_id

    def report_data(self, message_id: str, principal: Principal) -> dict | None:
        with self.database.connect() as connection:
            assistant = connection.execute(
                """SELECT m.*, c.title FROM messages m
                   JOIN conversations c ON c.id = m.conversation_id
                   WHERE m.id = ? AND m.role = 'assistant'
                     AND c.tenant_id = ? AND c.owner_subject = ?""",
                (message_id, principal.tenant_id, principal.subject),
            ).fetchone()
            if not assistant:
                return None
            question = connection.execute(
                """SELECT content FROM messages WHERE conversation_id = ? AND role = 'user'
                   AND created_at <= ? ORDER BY created_at DESC LIMIT 1""",
                (assistant["conversation_id"], assistant["created_at"]),
            ).fetchone()
        data = dict(assistant)
        data["question"] = question["content"] if question else ""
        data["citations"] = json.loads(data["citations_json"])
        return data

    def record_answer_diagnostics(
        self,
        principal: Principal,
        message_id: str,
        retrieval: RetrievalSummary,
        grounded: bool,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO answer_diagnostics
                   (message_id, tenant_id, retrieval_mode, authorized_candidates,
                    lexical_hits, semantic_hits, returned_chunks, best_semantic_score,
                    best_rerank_score, duration_ms, grounded, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    principal.tenant_id,
                    retrieval.mode,
                    retrieval.authorized_candidates,
                    retrieval.lexical_hits,
                    retrieval.semantic_hits,
                    retrieval.returned_chunks,
                    retrieval.best_semantic_score,
                    retrieval.best_rerank_score,
                    retrieval.duration_ms,
                    int(grounded),
                    _now(),
                ),
            )

    def reserve_model_budget(
        self,
        principal: Principal,
        *,
        reserved_tokens: int,
        reserved_cost_microusd: int,
        daily_request_limit: int,
        daily_token_limit: int,
        daily_cost_limit_microusd: int,
    ) -> tuple[bool, str | None]:
        """Atomically reserve worst-case daily capacity before an external model call."""
        usage_date = datetime.now(UTC).date().isoformat()
        now = _now()
        with self.database.connect() as connection:
            insert = (
                """INSERT INTO model_usage_daily
                       (tenant_id, usage_date, request_count, reserved_tokens,
                        reserved_cost_microusd, updated_at)
                   VALUES (?, ?, 0, 0, 0, ?)
                   ON CONFLICT(tenant_id, usage_date) DO NOTHING"""
                if self.database.dialect == "postgres"
                else """INSERT OR IGNORE INTO model_usage_daily
                       (tenant_id, usage_date, request_count, reserved_tokens,
                        reserved_cost_microusd, updated_at)
                   VALUES (?, ?, 0, 0, 0, ?)"""
            )
            connection.execute(insert, (principal.tenant_id, usage_date, now))
            cursor = connection.execute(
                """UPDATE model_usage_daily
                   SET request_count = request_count + 1,
                       reserved_tokens = reserved_tokens + ?,
                       reserved_cost_microusd = reserved_cost_microusd + ?,
                       updated_at = ?
                   WHERE tenant_id = ? AND usage_date = ?
                     AND request_count + 1 <= ?
                     AND reserved_tokens + ? <= ?
                     AND reserved_cost_microusd + ? <= ?""",
                (
                    reserved_tokens,
                    reserved_cost_microusd,
                    now,
                    principal.tenant_id,
                    usage_date,
                    daily_request_limit,
                    reserved_tokens,
                    daily_token_limit,
                    reserved_cost_microusd,
                    daily_cost_limit_microusd,
                ),
            )
            if cursor.rowcount == 1:
                return True, None
            current = connection.execute(
                """SELECT request_count, reserved_tokens, reserved_cost_microusd
                   FROM model_usage_daily WHERE tenant_id = ? AND usage_date = ?""",
                (principal.tenant_id, usage_date),
            ).fetchone()
        if current and current["request_count"] + 1 > daily_request_limit:
            return False, "daily_request_limit"
        if current and current["reserved_tokens"] + reserved_tokens > daily_token_limit:
            return False, "daily_token_limit"
        return False, "daily_cost_limit"

    def record_model_usage(
        self,
        *,
        event_id: str,
        principal: Principal,
        subject_hash: str,
        provider: str,
        model: str,
        status: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_microusd: int,
        latency_ms: float,
        fallback_reason: str | None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO model_usage_events
                   (id, tenant_id, subject_hash, provider, model, status,
                    input_tokens, output_tokens, total_tokens,
                    estimated_cost_microusd, latency_ms, fallback_reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    principal.tenant_id,
                    subject_hash,
                    provider,
                    model,
                    status,
                    input_tokens,
                    output_tokens,
                    input_tokens + output_tokens,
                    estimated_cost_microusd,
                    latency_ms,
                    fallback_reason,
                    _now(),
                ),
            )

    def admin_metrics(self, principal: Principal) -> dict:
        with self.database.connect() as connection:
            documents = connection.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active,
                          SUM(CASE WHEN status='archived' THEN 1 ELSE 0 END) archived,
                          SUM(CASE WHEN extraction_method LIKE '%ocr%' THEN 1 ELSE 0 END) ocr_documents,
                          COALESCE(SUM(word_count),0) words
                   FROM documents WHERE tenant_id = ?""",
                (principal.tenant_id,),
            ).fetchone()
            answers = connection.execute(
                """SELECT COUNT(*) total,
                          COALESCE(SUM(CASE WHEN grounded=0 THEN 1 ELSE 0 END),0) refused
                   FROM messages m JOIN conversations c ON c.id=m.conversation_id
                   WHERE c.tenant_id = ? AND m.role='assistant'""",
                (principal.tenant_id,),
            ).fetchone()
            feedback_rows = connection.execute(
                """SELECT category, COUNT(*) count FROM feedback
                   WHERE tenant_id = ? GROUP BY category""",
                (principal.tenant_id,),
            ).fetchall()
            pilot_reviews = connection.execute(
                """SELECT COUNT(*) review_count,
                          COALESCE(AVG(ease_score), 0) avg_ease_score,
                          COALESCE(AVG(trust_score), 0) avg_trust_score,
                          COALESCE(AVG(usefulness_score), 0) avg_usefulness_score,
                          COALESCE(AVG(completed_tasks), 0) avg_completed_tasks,
                          COALESCE(SUM(CASE WHEN would_return='yes' THEN 1 ELSE 0 END), 0)
                              would_return_yes,
                          COALESCE(SUM(CASE WHEN would_return='maybe' THEN 1 ELSE 0 END), 0)
                              would_return_maybe,
                          COALESCE(SUM(CASE WHEN would_return='no' THEN 1 ELSE 0 END), 0)
                              would_return_no
                   FROM pilot_reviews WHERE tenant_id = ?""",
                (principal.tenant_id,),
            ).fetchone()
            recent_audit = connection.execute(
                """SELECT event_type, entity_type, entity_id, detail_json, created_at
                   FROM audit_events WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 25""",
                (principal.tenant_id,),
            ).fetchall()
            payment_rows = connection.execute(
                """SELECT event_name, provider_id, status, amount_minor, currency,
                          customer_hint, test_mode, created_at
                   FROM payment_events WHERE tenant_id = ?
                   ORDER BY created_at DESC LIMIT 20""",
                (principal.tenant_id,),
            ).fetchall()
            payment_totals = connection.execute(
                """SELECT currency, COALESCE(SUM(amount_minor), 0) amount_minor,
                          COUNT(*) payment_count
                   FROM payment_events
                   WHERE tenant_id = ? AND event_name = 'order_created' AND test_mode = 0
                   GROUP BY currency""",
                (principal.tenant_id,),
            ).fetchall()
            retrieval = connection.execute(
                """SELECT COUNT(*) queries,
                          COALESCE(AVG(authorized_candidates), 0) avg_authorized_candidates,
                          COALESCE(AVG(lexical_hits), 0) avg_lexical_hits,
                          COALESCE(AVG(semantic_hits), 0) avg_semantic_hits,
                          COALESCE(AVG(returned_chunks), 0) avg_returned_chunks,
                          COALESCE(AVG(best_semantic_score), 0) avg_best_semantic_score,
                          COALESCE(AVG(best_rerank_score), 0) avg_best_rerank_score,
                          COALESCE(AVG(duration_ms), 0) avg_duration_ms,
                          COALESCE(SUM(CASE WHEN grounded=0 THEN 1 ELSE 0 END), 0) refusals
                   FROM answer_diagnostics WHERE tenant_id = ?""",
                (principal.tenant_id,),
            ).fetchone()
            model_usage = connection.execute(
                """SELECT COUNT(*) requests,
                          COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) completed,
                          COALESCE(SUM(CASE WHEN status!='completed' THEN 1 ELSE 0 END),0) fallbacks,
                          COALESCE(SUM(input_tokens),0) input_tokens,
                          COALESCE(SUM(output_tokens),0) output_tokens,
                          COALESCE(SUM(estimated_cost_microusd),0) estimated_cost_microusd,
                          COALESCE(AVG(latency_ms),0) avg_latency_ms
                   FROM model_usage_events WHERE tenant_id = ?""",
                (principal.tenant_id,),
            ).fetchone()
            model_usage_today = connection.execute(
                """SELECT request_count, reserved_tokens, reserved_cost_microusd
                   FROM model_usage_daily WHERE tenant_id = ? AND usage_date = ?""",
                (principal.tenant_id, datetime.now(UTC).date().isoformat()),
            ).fetchone()
            ingestion_rows = connection.execute(
                """SELECT stage, COUNT(*) count FROM ingestion_jobs
                   WHERE tenant_id = ? GROUP BY stage""",
                (principal.tenant_id,),
            ).fetchall()
            oldest_pending = connection.execute(
                """SELECT created_at FROM ingestion_jobs
                   WHERE tenant_id = ? AND status IN ('queued','processing')
                   ORDER BY created_at LIMIT 1""",
                (principal.tenant_id,),
            ).fetchone()
        return {
            "documents": dict(documents),
            "answers": dict(answers),
            "feedback": {row["category"]: row["count"] for row in feedback_rows},
            "pilot_reviews": dict(pilot_reviews),
            "retrieval": dict(retrieval),
            "model_usage": {
                **dict(model_usage),
                "today": dict(model_usage_today) if model_usage_today else {
                    "request_count": 0,
                    "reserved_tokens": 0,
                    "reserved_cost_microusd": 0,
                },
            },
            "ingestion": {
                "by_stage": {row["stage"]: row["count"] for row in ingestion_rows},
                "pending": sum(
                    row["count"]
                    for row in ingestion_rows
                    if row["stage"]
                    in {"queued", "security_check", "scanning", "extracting", "ocr", "indexing"}
                ),
                "quarantined": sum(
                    row["count"] for row in ingestion_rows if row["stage"] == "quarantined"
                ),
                "dead_letter": sum(
                    row["count"] for row in ingestion_rows if row["stage"] == "dead_letter"
                ),
                "oldest_pending_at": oldest_pending["created_at"] if oldest_pending else None,
            },
            "recent_audit": [
                {**dict(row), "detail": json.loads(row["detail_json"])} for row in recent_audit
            ],
            "payments": {
                "totals": [dict(row) for row in payment_totals],
                "recent": [dict(row) for row in payment_rows],
            },
        }

    def record_payment_event(
        self, event: PaymentEvent, *, tenant_id: str, provider: str = "lemonsqueezy"
    ) -> bool:
        with self.database.connect() as connection:
            insert = (
                """INSERT INTO payment_events(
                       event_key, tenant_id, provider, event_name, provider_id, status,
                       amount_minor, currency, customer_hint, test_mode, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_key) DO NOTHING"""
                if self.database.dialect == "postgres"
                else """INSERT OR IGNORE INTO payment_events(
                       event_key, tenant_id, provider, event_name, provider_id, status,
                       amount_minor, currency, customer_hint, test_mode, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
            )
            cursor = connection.execute(
                insert,
                (
                    event.event_key,
                    tenant_id,
                    provider,
                    event.event_name,
                    event.provider_id,
                    event.status,
                    event.amount_minor,
                    event.currency,
                    event.customer_hint,
                    int(event.test_mode),
                    _now(),
                ),
            )
            return cursor.rowcount == 1

    def apply_workspace_entitlement(
        self, decision: EntitlementDecision, *, provider: str = "lemonsqueezy"
    ) -> bool:
        """Apply an already verified plan decision and ignore stale provider updates."""
        now = _now()
        with self.database.connect() as connection:
            workspace = connection.execute(
                "SELECT id FROM workspaces WHERE id = ?", (decision.workspace_id,)
            ).fetchone()
            if not workspace:
                return False
            cursor = connection.execute(
                """INSERT INTO workspace_entitlements(
                       workspace_id, provider, provider_id, variant_id, status, plan,
                       provider_updated_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, provider_id) DO UPDATE SET
                       variant_id=excluded.variant_id,
                       status=excluded.status,
                       plan=excluded.plan,
                       provider_updated_at=excluded.provider_updated_at,
                       updated_at=excluded.updated_at
                   WHERE workspace_entitlements.workspace_id = excluded.workspace_id
                     AND (workspace_entitlements.provider_updated_at = ''
                      OR (excluded.provider_updated_at <> '' AND
                          excluded.provider_updated_at > workspace_entitlements.provider_updated_at))""",
                (
                    decision.workspace_id,
                    provider,
                    decision.provider_id,
                    decision.variant_id,
                    decision.status,
                    decision.plan,
                    decision.provider_updated_at,
                    now,
                ),
            )
            entitlement = connection.execute(
                """SELECT CASE WHEN COUNT(*) > 0 THEN 'pro' ELSE 'free' END plan
                   FROM workspace_entitlements
                   WHERE workspace_id = ? AND plan = 'pro'""",
                (decision.workspace_id,),
            ).fetchone()
            assert entitlement is not None
            connection.execute(
                "UPDATE workspaces SET plan = ? WHERE id = ?",
                (entitlement["plan"], decision.workspace_id),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    """INSERT INTO audit_events(
                           tenant_id, subject, event_type, entity_type, entity_id,
                           detail_json, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        decision.workspace_id,
                        "lemonsqueezy-webhook",
                        "workspace_entitlement_updated",
                        "workspace",
                        decision.workspace_id,
                        json.dumps(
                            {
                                "plan": decision.plan,
                                "status": decision.status,
                                "provider": provider,
                            },
                            separators=(",", ":"),
                        ),
                        now,
                    ),
                )
            return cursor.rowcount == 1
