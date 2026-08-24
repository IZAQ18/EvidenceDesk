from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    source TEXT,
    classification TEXT NOT NULL CHECK (classification IN ('public','internal','confidential')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 1,
    word_count INTEGER NOT NULL DEFAULT 0,
    extraction_method TEXT NOT NULL DEFAULT 'legacy',
    extraction_warning TEXT,
    collection TEXT,
    version TEXT,
    effective_date TEXT,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    storage_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_dedupe
    ON documents(tenant_id, sha256, classification);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    classification TEXT NOT NULL,
    injection_flag INTEGER NOT NULL DEFAULT 0,
    page_number INTEGER,
    UNIQUE(document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_policy ON chunks(tenant_id, classification, document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS document_pages (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    ocr_used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(document_id, page_number)
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_subject TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_owner
    ON conversations(tenant_id, owner_subject, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    grounded INTEGER,
    evidence_quality TEXT,
    citations_json TEXT NOT NULL DEFAULT '[]',
    workflow_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(subject, message_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS payment_events (
    event_key TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    event_name TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_minor INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL,
    customer_hint TEXT,
    test_mode INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payment_events_tenant
    ON payment_events(tenant_id, created_at DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Small additive migrations keep existing local MVP databases usable."""
        document_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(documents)")
        }
        chunk_columns = {row["name"] for row in connection.execute("PRAGMA table_info(chunks)")}
        additions = {
            "page_count": "INTEGER NOT NULL DEFAULT 1",
            "word_count": "INTEGER NOT NULL DEFAULT 0",
            "extraction_method": "TEXT NOT NULL DEFAULT 'legacy'",
            "extraction_warning": "TEXT",
            "collection": "TEXT",
            "version": "TEXT",
            "effective_date": "TEXT",
            "expires_at": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "storage_path": "TEXT",
        }
        for name, definition in additions.items():
            if name not in document_columns:
                connection.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
        if "page_number" not in chunk_columns:
            connection.execute("ALTER TABLE chunks ADD COLUMN page_number INTEGER")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
