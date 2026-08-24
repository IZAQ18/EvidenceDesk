from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

POSTGRES_URL = os.getenv("EVIDENCEDESK_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL integration URL not configured")


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
                """TRUNCATE model_usage_events, model_usage_daily,
                          auth_sessions, workspace_members, workspaces, users,
                          payment_events, audit_events, feedback, answer_diagnostics,
                          messages, conversations,
                          ingestion_jobs, document_pages, chunks, documents
                          RESTART IDENTITY CASCADE"""
            )
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        assert [row["version"] for row in versions] == [1, 2, 3, 4]

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
            item for item in client.get("/v1/documents").json()
            if item["id"] == job["document_id"]
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
