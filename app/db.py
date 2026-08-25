from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings

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

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','disabled','pending_verification')),
    email_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('reader','editor','admin')),
    clearances_json TEXT NOT NULL DEFAULT '["public","internal","confidential"]',
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_members_user
    ON workspace_members(user_id, workspace_id);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);

CREATE TABLE IF NOT EXISTS account_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('verify_email','reset_password')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_account_tokens_user
    ON account_tokens(user_id, purpose, expires_at);

CREATE TABLE IF NOT EXISTS workspace_invitations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('reader','editor','admin')),
    clearances_json TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    invited_by TEXT NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK (status IN ('pending','accepted','revoked')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    accepted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workspace_invitations_lookup
    ON workspace_invitations(workspace_id, email, status, expires_at);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    owner_subject TEXT NOT NULL,
    owner_role TEXT NOT NULL,
    owner_clearances_json TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('public','internal','confidential')),
    source TEXT,
    collection TEXT,
    version TEXT,
    effective_date TEXT,
    expires_at TEXT,
    supersedes_document_id TEXT,
    storage_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','processing','ready','failed')),
    stage TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    worker_id TEXT,
    lease_expires_at TEXT,
    error_code TEXT,
    error_message TEXT,
    error_action TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_queue
    ON ingestion_jobs(status, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_tenant
    ON ingestion_jobs(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS answer_diagnostics (
    message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL,
    authorized_candidates INTEGER NOT NULL DEFAULT 0,
    lexical_hits INTEGER NOT NULL DEFAULT 0,
    semantic_hits INTEGER NOT NULL DEFAULT 0,
    returned_chunks INTEGER NOT NULL DEFAULT 0,
    best_semantic_score REAL NOT NULL DEFAULT 0,
    best_rerank_score REAL NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0,
    grounded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_answer_diagnostics_tenant
    ON answer_diagnostics(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS model_usage_daily (
    tenant_id TEXT NOT NULL,
    usage_date TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    reserved_tokens INTEGER NOT NULL DEFAULT 0,
    reserved_cost_microusd INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, usage_date)
);

CREATE TABLE IF NOT EXISTS model_usage_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subject_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_microusd INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_usage_events_tenant
    ON model_usage_events(tenant_id, created_at DESC);
"""

POSTGRES_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes BIGINT NOT NULL,
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
            )""",
            "CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id, created_at DESC)",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_dedupe
               ON documents(tenant_id, sha256, classification)""",
            """CREATE TABLE IF NOT EXISTS chunks (
                id BIGSERIAL PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                classification TEXT NOT NULL,
                injection_flag INTEGER NOT NULL DEFAULT 0,
                page_number INTEGER,
                UNIQUE(document_id, chunk_index)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_chunks_policy ON chunks(tenant_id, classification, document_id)",
            """CREATE INDEX IF NOT EXISTS idx_chunks_search
               ON chunks USING GIN (to_tsvector('simple', text))""",
            """CREATE TABLE IF NOT EXISTS document_pages (
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                text TEXT NOT NULL,
                ocr_used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(document_id, page_number)
            )""",
            """CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                owner_subject TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_conversations_owner
               ON conversations(tenant_id, owner_subject, updated_at DESC)""",
            """CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                content TEXT NOT NULL,
                grounded INTEGER,
                evidence_quality TEXT,
                citations_json TEXT NOT NULL DEFAULT '[]',
                workflow_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at)",
            """CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                category TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(subject, message_id)
            )""",
            """CREATE TABLE IF NOT EXISTS audit_events (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id, created_at DESC)",
            """CREATE TABLE IF NOT EXISTS payment_events (
                event_key TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                event_name TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                status TEXT NOT NULL,
                amount_minor BIGINT NOT NULL DEFAULT 0,
                currency TEXT NOT NULL,
                customer_hint TEXT,
                test_mode INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_payment_events_tenant ON payment_events(tenant_id, created_at DESC)",
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','disabled','pending_verification')),
                email_verified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_ci ON users(LOWER(email))",
            """CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                plan TEXT NOT NULL DEFAULT 'free',
                created_by TEXT NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS workspace_members (
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('reader','editor','admin')),
                clearances_json TEXT NOT NULL DEFAULT '[\"public\",\"internal\",\"confidential\"]',
                created_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id, user_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id, workspace_id)",
            """CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                csrf_token TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at)",
        ),
    ),
    (
        2,
        (
            """CREATE TABLE IF NOT EXISTS ingestion_jobs (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                owner_subject TEXT NOT NULL,
                owner_role TEXT NOT NULL,
                owner_clearances_json TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes BIGINT NOT NULL,
                sha256 TEXT NOT NULL,
                classification TEXT NOT NULL CHECK (classification IN ('public','internal','confidential')),
                source TEXT,
                collection TEXT,
                version TEXT,
                effective_date TEXT,
                expires_at TEXT,
                supersedes_document_id TEXT,
                storage_path TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued','processing','ready','failed')),
                stage TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                worker_id TEXT,
                lease_expires_at TEXT,
                error_code TEXT,
                error_message TEXT,
                error_action TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )""",
            """CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_queue
               ON ingestion_jobs(status, lease_expires_at, created_at)""",
            """CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_tenant
               ON ingestion_jobs(tenant_id, created_at DESC)""",
        ),
    ),
    (
        3,
        (
            """CREATE TABLE IF NOT EXISTS answer_diagnostics (
                message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL,
                retrieval_mode TEXT NOT NULL,
                authorized_candidates INTEGER NOT NULL DEFAULT 0,
                lexical_hits INTEGER NOT NULL DEFAULT 0,
                semantic_hits INTEGER NOT NULL DEFAULT 0,
                returned_chunks INTEGER NOT NULL DEFAULT 0,
                best_semantic_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                best_rerank_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
                grounded INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_answer_diagnostics_tenant
               ON answer_diagnostics(tenant_id, created_at DESC)""",
        ),
    ),
    (
        4,
        (
            """CREATE TABLE IF NOT EXISTS model_usage_daily (
                tenant_id TEXT NOT NULL,
                usage_date TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                reserved_tokens BIGINT NOT NULL DEFAULT 0,
                reserved_cost_microusd BIGINT NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(tenant_id, usage_date)
            )""",
            """CREATE TABLE IF NOT EXISTS model_usage_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                subject_hash TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                input_tokens BIGINT NOT NULL DEFAULT 0,
                output_tokens BIGINT NOT NULL DEFAULT 0,
                total_tokens BIGINT NOT NULL DEFAULT 0,
                estimated_cost_microusd BIGINT NOT NULL DEFAULT 0,
                latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
                fallback_reason TEXT,
                created_at TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_model_usage_events_tenant
               ON model_usage_events(tenant_id, created_at DESC)""",
        ),
    ),
    (
        5,
        (
            """CREATE TABLE IF NOT EXISTS account_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL CHECK (purpose IN ('verify_email','reset_password')),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT
            )""",
            """CREATE INDEX IF NOT EXISTS idx_account_tokens_user
               ON account_tokens(user_id, purpose, expires_at)""",
        ),
    ),
    (
        6,
        (
            """CREATE TABLE IF NOT EXISTS workspace_invitations (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                email TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('reader','editor','admin')),
                clearances_json TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                invited_by TEXT NOT NULL REFERENCES users(id),
                status TEXT NOT NULL CHECK (status IN ('pending','accepted','revoked')),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                accepted_at TEXT
            )""",
            """CREATE INDEX IF NOT EXISTS idx_workspace_invitations_lookup
               ON workspace_invitations(workspace_id, email, status, expires_at)""",
        ),
    ),
)


class Database:
    dialect = "sqlite"

    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    def ping(self) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1").fetchone()[0] == 1

    def close(self) -> None:
        return None

    @staticmethod
    def is_unique_violation(exc: Exception) -> bool:
        return isinstance(exc, sqlite3.IntegrityError)

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


class PostgresConnection:
    def __init__(self, connection: Any):
        self.connection = connection

    @staticmethod
    def _translate(query: str) -> str:
        return query.replace("%", "%%").replace("?", "%s")

    def execute(self, query: str, parameters: Sequence[Any] | None = None) -> Any:
        if parameters is None:
            return self.connection.execute(query)
        return self.connection.execute(self._translate(query), tuple(parameters))


class PostgresDatabase:
    dialect = "postgres"

    def __init__(
        self,
        url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        timeout: float = 10.0,
    ):
        self.url = url
        self.min_size = min_size
        self.max_size = max_size
        self.timeout = timeout
        self.pool: Any | None = None

    def _driver(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError("Install the PostgreSQL dependencies from requirements.txt") from exc
        return psycopg, dict_row, ConnectionPool

    def _ensure_pool(self) -> Any:
        if self.pool is not None:
            return self.pool
        _, dict_row, connection_pool = self._driver()
        self.pool = connection_pool(
            conninfo=self.url,
            min_size=self.min_size,
            max_size=self.max_size,
            timeout=self.timeout,
            kwargs={"row_factory": dict_row, "connect_timeout": 10},
            check=connection_pool.check_connection,
            open=False,
            name="evidencedesk",
        )
        return self.pool

    def initialize(self) -> None:
        pool = self._ensure_pool()
        pool.open(wait=True, timeout=self.timeout)
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(8242026)")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version INTEGER PRIMARY KEY,
                       applied_at TEXT NOT NULL
                   )"""
            )
            applied = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            }
            for version, statements in POSTGRES_MIGRATIONS:
                if version in applied:
                    continue
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(UTC).isoformat()),
                )

    def ping(self) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1 AS ready").fetchone()["ready"] == 1

    def close(self) -> None:
        if self.pool is not None and not self.pool.closed:
            self.pool.close(timeout=5.0)

    @staticmethod
    def is_unique_violation(exc: Exception) -> bool:
        return getattr(exc, "sqlstate", None) == "23505"

    @contextmanager
    def connect(self) -> Iterator[PostgresConnection]:
        pool = self._ensure_pool()
        if pool.closed:
            raise RuntimeError("PostgreSQL connection pool is not open")
        with pool.connection(timeout=self.timeout) as connection:
            yield PostgresConnection(connection)


DatabaseLike = Database | PostgresDatabase


def build_database(settings: Settings) -> DatabaseLike:
    settings.validate_database()
    if settings.database_backend == "postgres":
        return PostgresDatabase(
            settings.resolved_database_url(),
            min_size=settings.database_pool_min_size,
            max_size=settings.database_pool_max_size,
            timeout=settings.database_pool_timeout_seconds,
        )
    return Database(settings.db_path)
