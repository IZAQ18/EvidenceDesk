from __future__ import annotations

import hashlib
import os
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database, PostgresDatabase, build_database
from app.ingestion import ExtractedDocument, ExtractedPage, chunk_pages
from app.main import create_app
from app.migrate_data import migrate
from app.models import Principal
from app.repository import Repository
from app.storage import LocalDocumentStorage

POSTGRES_URL = os.getenv("EVIDENCEDESK_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="PostgreSQL integration URL not configured"
)


class MemoryS3Storage:
    objects: dict[str, bytes] = {}

    def __init__(self, *, prefix: str, **_: object):
        self.prefix = prefix

    def save(
        self,
        tenant_id: str,
        document_id: str,
        extension: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        del content_type
        reference = f"s3:{self.prefix}/{tenant_id}/{document_id}{extension}"
        self.objects[reference] = content
        return reference

    def stream(self, reference: str):
        return [self.objects[reference]]

    def ping(self) -> bool:
        return True


class CapturingAccountEmail:
    def __init__(self):
        self.verification_urls: list[str] = []
        self.reset_urls: list[str] = []
        self.invitation_urls: list[str] = []

    def send_verification(self, recipient: str, display_name: str, url: str) -> None:
        del recipient, display_name
        self.verification_urls.append(url)

    def send_password_reset(self, recipient: str, display_name: str, url: str) -> None:
        del recipient, display_name
        self.reset_urls.append(url)

    def send_invitation(self, recipient: str, workspace_name: str, url: str) -> None:
        del recipient, workspace_name
        self.invitation_urls.append(url)


def _link_token(url: str) -> str:
    return parse_qs(urlsplit(url).query)["token"][0]


def _register(client: TestClient, email: str, name: str) -> dict:
    response = client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "display_name": name,
            "password": "integration-correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_postgres_accounts_ingestion_retrieval_and_tenant_isolation(tmp_path):
    settings = Settings(
        _env_file=None,
        env="test",
        database_backend="postgres",
        database_url=POSTGRES_URL,
        document_store_path=tmp_path / "originals",
        accounts_enabled=True,
        account_registration_enabled=True,
        rate_limit_per_minute=10_000,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        database = app.state.repository.database
        with database.connect() as connection:
            connection.execute(
                """TRUNCATE funnel_daily, model_usage_events, model_usage_daily,
                          workspace_invitations, account_tokens, auth_sessions,
                          workspace_usage_monthly, workspace_entitlements,
                          workspace_members, workspaces, users,
                          payment_events, audit_events, feedback, answer_diagnostics,
                          messages, conversations,
                          ingestion_jobs, document_pages, chunks, documents
                          RESTART IDENTITY CASCADE"""
            )
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        assert [row["version"] for row in versions] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

        first = _register(client, "FIRST@example.com", "First Team")
        uploaded = client.post(
            "/v1/ingestion-jobs",
            headers={"X-CSRF-Token": first["csrf_token"]},
            files={
                "file": (
                    "leave-policy.txt",
                    b"Employees receive twenty paid annual leave days every calendar year.",
                    "text/plain",
                )
            },
            data={"classification": "internal", "source": "People Operations"},
        )
        assert uploaded.status_code == 202, uploaded.text
        job = uploaded.json()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = client.get(f"/v1/ingestion-jobs/{job['id']}").json()
            if job["status"] == "ready":
                break
            time.sleep(0.05)
        assert job["status"] == "ready", job
        document = next(
            item for item in client.get("/v1/documents").json() if item["id"] == job["document_id"]
        )
        assert document["has_original"] is True

        answer = client.post(
            "/v1/answers",
            headers={"X-CSRF-Token": first["csrf_token"]},
            json={"question": "How many annual vacation days do employees receive?"},
        )
        assert answer.status_code == 200, answer.text
        result = answer.json()
        assert result["grounded"] is True
        assert result["citations"][0]["document_id"] == document["id"]
        assert "twenty" in result["answer"].lower()
        assert result["retrieval"]["semantic_hits"] >= 1
        admin_metrics = client.get("/v1/admin/metrics").json()
        quality_metrics = admin_metrics["retrieval"]
        assert quality_metrics["queries"] == 1
        assert quality_metrics["avg_best_rerank_score"] > 0
        assert admin_metrics["model_usage"]["completed"] == 0
        assert client.get(f"/v1/documents/{document['id']}/content").content.startswith(
            b"Employees receive twenty"
        )

        second = _register(client, "second@example.com", "Second Team")
        assert second["workspace_id"] != first["workspace_id"]
        assert client.get("/v1/documents").json() == []
        isolated = client.post(
            "/v1/answers",
            headers={"X-CSRF-Token": second["csrf_token"]},
            json={"question": "How many annual vacation days do employees receive?"},
        ).json()
        assert isolated["grounded"] is False
        assert isolated["citations"] == []

        login = client.post(
            "/v1/auth/login",
            json={
                "email": "first@EXAMPLE.com",
                "password": "integration-correct-horse-battery-staple",
            },
        )
        assert login.status_code == 200, login.text
        assert login.json()["workspace_id"] == first["workspace_id"]
        assert len(client.get("/v1/documents").json()) == 1
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/ready").json() == {
            "status": "ready",
            "checks": {"database": True, "original_storage": True},
        }


def test_sqlite_and_local_originals_migrate_to_postgres_and_s3(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        env="test",
        database_backend="postgres",
        database_url=POSTGRES_URL,
        document_store_backend="s3",
        s3_bucket="evidencedesk-integration",
        ingestion_worker_enabled=False,
    )
    target = build_database(settings)
    assert isinstance(target, PostgresDatabase)
    target.initialize()
    with target.connect() as connection:
        connection.execute(
            """TRUNCATE funnel_daily, model_usage_events, model_usage_daily,
                      workspace_invitations, account_tokens, auth_sessions,
                      workspace_usage_monthly, workspace_entitlements,
                      workspace_members, workspaces, users,
                      payment_events, audit_events, feedback, answer_diagnostics,
                      messages, conversations, ingestion_jobs, document_pages,
                      chunks, documents RESTART IDENTITY CASCADE"""
        )
    target.close()

    source_path = tmp_path / "migration-source.db"
    originals_path = tmp_path / "migration-originals"
    source = Database(source_path)
    source.initialize()
    repository = Repository(source)
    originals = LocalDocumentStorage(originals_path)
    content = b"Synthetic staff receive twenty days of annual leave."
    extracted = ExtractedDocument(
        filename="synthetic-leave.txt",
        content_type="text/plain",
        content=content,
        pages=[ExtractedPage(page_number=1, text=content.decode())],
        sha256=hashlib.sha256(content).hexdigest(),
        extraction_method="postgres-migration-integration",
    )
    principal = Principal(
        subject="migration-admin",
        tenant_id="migration-workspace",
        role="admin",
        clearances=["public", "internal", "confidential"],
    )
    reference = originals.save(
        principal.tenant_id, "migration-document", ".txt", content, "text/plain"
    )
    repository.add_document(
        extracted,
        chunk_pages(extracted.pages),
        "internal",
        "Synthetic integration source",
        principal,
        document_id="migration-document",
        storage_path=reference,
    )

    MemoryS3Storage.objects = {}
    monkeypatch.setattr("app.migrate_data.S3DocumentStorage", MemoryS3Storage)
    report = migrate(
        source_db_path=source_path,
        source_originals_path=originals_path,
        settings=settings,
        execute=True,
        migration_id="ci_test",
    )
    assert report["verified"] is True
    assert report["target_rows"]["documents"] == 1
    assert report["target_rows"]["chunks"] == 1
    assert report["target_prefix"] == "evidencedesk/originals/migration-ci_test"
    assert list(MemoryS3Storage.objects.values()) == [content]

    verification = build_database(settings)
    verification.initialize()
    with verification.connect() as connection:
        document = connection.execute(
            "SELECT storage_path, sha256 FROM documents WHERE id = ?",
            ("migration-document",),
        ).fetchone()
    verification.close()
    assert document["storage_path"].startswith("s3:evidencedesk/originals/migration-ci_test/")
    assert document["sha256"] == hashlib.sha256(content).hexdigest()


def test_postgres_email_verification_and_password_recovery(tmp_path):
    settings = Settings(
        _env_file=None,
        env="test",
        database_backend="postgres",
        database_url=POSTGRES_URL,
        document_store_path=tmp_path / "account-originals",
        accounts_enabled=True,
        account_registration_enabled=True,
        account_email_verification_required=True,
        account_password_reset_enabled=True,
        public_base_url="https://accounts.example.test",
        ingestion_worker_enabled=False,
        rate_limit_per_minute=10_000,
    )
    sender = CapturingAccountEmail()
    app = create_app(settings, account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        database = app.state.repository.database
        with database.connect() as connection:
            connection.execute(
                """TRUNCATE funnel_daily, model_usage_events, model_usage_daily,
                          workspace_invitations, account_tokens, auth_sessions,
                          workspace_usage_monthly, workspace_entitlements,
                          workspace_members, workspaces, users,
                          payment_events, audit_events, feedback, answer_diagnostics,
                          messages, conversations, ingestion_jobs, document_pages,
                          chunks, documents RESTART IDENTITY CASCADE"""
            )

        registered = client.post(
            "/v1/auth/register",
            json={
                "email": "postgres-recovery@example.com",
                "display_name": "Postgres Recovery",
                "password": "postgres-original-correct-password",
            },
        )
        assert registered.status_code == 202
        verification_token = _link_token(sender.verification_urls[-1])
        verified = client.post("/v1/auth/verify-email", json={"token": verification_token})
        assert verified.status_code == 200

        requested = client.post(
            "/v1/auth/password-reset/request",
            json={"email": "postgres-recovery@example.com"},
        )
        assert requested.status_code == 202
        reset_token = _link_token(sender.reset_urls[-1])
        completed = client.post(
            "/v1/auth/password-reset/complete",
            json={"token": reset_token, "password": "postgres-new-correct-password"},
        )
        assert completed.status_code == 200
        assert client.get("/v1/auth/me").status_code == 401
        login = client.post(
            "/v1/auth/login",
            json={
                "email": "postgres-recovery@example.com",
                "password": "postgres-new-correct-password",
            },
        )
        assert login.status_code == 200


def test_postgres_workspace_invitation_and_membership_switch(tmp_path):
    settings = Settings(
        _env_file=None,
        env="test",
        database_backend="postgres",
        database_url=POSTGRES_URL,
        document_store_path=tmp_path / "account-controls-originals",
        accounts_enabled=True,
        account_registration_enabled=True,
        account_invitations_enabled=True,
        account_deletion_enabled=True,
        public_base_url="https://accounts.example.test",
        ingestion_worker_enabled=False,
        rate_limit_per_minute=10_000,
    )
    sender = CapturingAccountEmail()
    app = create_app(settings, account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        database = app.state.repository.database
        with database.connect() as connection:
            connection.execute(
                """TRUNCATE funnel_daily, model_usage_events, model_usage_daily,
                          workspace_invitations, account_tokens, auth_sessions,
                          workspace_usage_monthly, workspace_entitlements,
                          workspace_members, workspaces, users,
                          payment_events, audit_events, feedback, answer_diagnostics,
                          messages, conversations, ingestion_jobs, document_pages,
                          chunks, documents RESTART IDENTITY CASCADE"""
            )

        owner = _register(client, "postgres-owner@example.com", "Postgres Owner")
        invited = client.post(
            "/v1/account/invitations",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={
                "email": "postgres-member@example.com",
                "role": "reader",
                "clearances": ["public", "internal"],
            },
        )
        assert invited.status_code == 201, invited.text
        invitation_token = _link_token(sender.invitation_urls[-1])
        client.cookies.clear()
        accepted = client.post(
            "/v1/auth/invitations/accept",
            json={
                "token": invitation_token,
                "display_name": "Postgres Member",
                "password": "postgres-member-correct-password",
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["workspace_id"] == owner["workspace_id"]
        assert accepted.json()["role"] == "reader"
        memberships = client.get("/v1/account/workspaces")
        assert memberships.status_code == 200
        assert memberships.json()[0]["member_count"] == 2

        client.cookies.clear()
        owner_login = client.post(
            "/v1/auth/login",
            json={
                "email": "postgres-owner@example.com",
                "password": "integration-correct-horse-battery-staple",
            },
        )
        assert owner_login.status_code == 200
        members = client.get("/v1/account/members")
        assert members.status_code == 200
        assert len(members.json()) == 2
        transferred = client.post(
            "/v1/account/ownership/transfer",
            headers={"X-CSRF-Token": owner_login.json()["csrf_token"]},
            json={
                "new_owner_user_id": accepted.json()["id"],
                "password": "integration-correct-horse-battery-staple",
                "confirmation": "TRANSFER",
            },
        )
        assert transferred.status_code == 204, transferred.text
        deleted = client.post(
            "/v1/account/delete",
            headers={"X-CSRF-Token": owner_login.json()["csrf_token"]},
            json={
                "password": "integration-correct-horse-battery-staple",
                "confirmation": "DELETE",
            },
        )
        assert deleted.status_code == 204, deleted.text
        with database.connect() as connection:
            workspace = connection.execute(
                "SELECT created_by FROM workspaces WHERE id = ?", (owner["workspace_id"],)
            ).fetchone()
            member_count = connection.execute(
                "SELECT COUNT(*) member_count FROM workspace_members WHERE workspace_id = ?",
                (owner["workspace_id"],),
            ).fetchone()["member_count"]
        assert workspace["created_by"] == accepted.json()["id"]
        assert member_count == 1
