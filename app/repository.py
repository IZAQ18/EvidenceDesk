from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.config import Classification
from app.db import Database
from app.ingestion import ExtractedDocument, TextChunk, has_injection_language
from app.models import Principal

QUERY_EXPANSIONS: dict[str, set[str]] = {
    "vacation": {"leave", "holiday", "absence"},
    "holiday": {"leave", "vacation"},
    "pto": {"leave", "vacation", "absence"},
    "leave": {"vacation", "absence", "holiday"},
    "complaint": {"report", "concern", "violation", "misconduct"},
    "misconduct": {"violation", "complaint", "ethics"},
    "report": {"notify", "disclose", "complaint"},
    "fire": {"terminate", "termination", "dismissal"},
    "salary": {"pay", "compensation", "wage"},
    "remote": {"hybrid", "telework", "home"},
    "privacy": {"confidential", "personal", "data"},
}


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


class DuplicateDocumentError(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[\w-]{2,}", text.lower()))


def _expanded_terms(question: str) -> tuple[list[str], set[str]]:
    exact = _terms(question)
    expanded = set(exact)
    for term in exact:
        expanded.update(QUERY_EXPANSIONS.get(term, set()))
    return list(dict.fromkeys([*exact, *sorted(expanded - exact)]))[:30], exact


class Repository:
    def __init__(self, database: Database):
        self.database = database

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
    ) -> str:
        document_id = document_id or str(uuid4())
        now = _now()
        try:
            with self.database.connect() as connection:
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
                    cursor = connection.execute(
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
                    connection.execute(
                        "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
                        (cursor.lastrowid, chunk.text),
                    )
                if supersedes_document_id:
                    connection.execute(
                        """UPDATE documents SET status = 'superseded'
                           WHERE id = ? AND tenant_id = ? AND status = 'active'""",
                        (supersedes_document_id, principal.tenant_id),
                    )
        except sqlite3.IntegrityError as exc:
            if "documents.tenant_id, documents.sha256" in str(exc):
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
            chunk_ids = [
                row[0]
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
        terms, exact_terms = _expanded_terms(question)
        if not terms:
            return []
        fts_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
        placeholders = ",".join("?" for _ in principal.clearances)
        scope_clause = ""
        scope_parameters: tuple[str, ...] = ()
        if document_ids:
            scope_placeholders = ",".join("?" for _ in document_ids)
            scope_clause = f"AND c.document_id IN ({scope_placeholders})"
            scope_parameters = tuple(document_ids)
        candidate_limit = max(top_k * 6, 24)
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
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()

        expanded_set = set(terms)
        ranked: list[RetrievedChunk] = []
        for rank, row in enumerate(rows):
            row_data = dict(row)
            text_terms = _terms(row_data["text"])
            exact_overlap = len(exact_terms & text_terms)
            expanded_overlap = len(expanded_set & text_terms) - exact_overlap
            metadata_terms = _terms(f"{row_data['filename']} {row_data['source'] or ''}")
            metadata_overlap = len(exact_terms & metadata_terms)
            phrase_boost = 0.35 if question.lower().strip() in row_data["text"].lower() else 0.0
            relevance = (
                exact_overlap * 1.0
                + max(0, expanded_overlap) * 0.35
                + metadata_overlap * 0.25
                + phrase_boost
                + 1 / (rank + 2)
            )
            row_data["injection_flag"] = bool(row_data["injection_flag"])
            ranked.append(RetrievedChunk(**row_data, relevance_score=round(relevance, 4)))
        ranked.sort(key=lambda item: item.relevance_score, reverse=True)
        return ranked[:top_k]

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

    def admin_metrics(self, principal: Principal) -> dict:
        with self.database.connect() as connection:
            documents = connection.execute(
                """SELECT COUNT(*) total,
                          SUM(status='active') active,
                          SUM(status='archived') archived,
                          SUM(extraction_method LIKE '%ocr%') ocr_documents,
                          COALESCE(SUM(word_count),0) words
                   FROM documents WHERE tenant_id = ?""",
                (principal.tenant_id,),
            ).fetchone()
            answers = connection.execute(
                """SELECT COUNT(*) total, COALESCE(SUM(grounded=0),0) refused
                   FROM messages m JOIN conversations c ON c.id=m.conversation_id
                   WHERE c.tenant_id = ? AND m.role='assistant'""",
                (principal.tenant_id,),
            ).fetchone()
            feedback_rows = connection.execute(
                """SELECT category, COUNT(*) count FROM feedback
                   WHERE tenant_id = ? GROUP BY category""",
                (principal.tenant_id,),
            ).fetchall()
            recent_audit = connection.execute(
                """SELECT event_type, entity_type, entity_id, detail_json, created_at
                   FROM audit_events WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 25""",
                (principal.tenant_id,),
            ).fetchall()
        return {
            "documents": dict(documents),
            "answers": dict(answers),
            "feedback": {row["category"]: row["count"] for row in feedback_rows},
            "recent_audit": [
                {**dict(row), "detail": json.loads(row["detail_json"])} for row in recent_audit
            ],
        }
