from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "plans.db",
        document_store_path=tmp_path / "originals",
        accounts_enabled=True,
        account_registration_enabled=True,
        ingestion_worker_enabled=False,
        rate_limit_per_minute=10_000,
    )


def _register(client: TestClient) -> dict:
    response = client.post(
        "/v1/auth/register",
        json={
            "email": "plan-owner@example.com",
            "display_name": "Plan Owner",
            "password": "a-correct-horse-battery-staple",
        },
    )
    assert response.status_code == 201
    return response.json()


def _upload(client: TestClient, account: dict, number: int):
    return client.post(
        "/v1/documents",
        headers={"X-CSRF-Token": account["csrf_token"]},
        files={
            "file": (
                f"policy-{number}.txt",
                f"Synthetic policy number {number} provides twenty annual leave days.".encode(),
                "text/plain",
            )
        },
        data={"classification": "internal"},
    )


def test_free_and_pro_document_allowances_are_enforced_server_side(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        account = _register(client)
        initial = client.get("/v1/account/usage").json()
        assert initial["managed"] is True
        assert initial["plan"] == "free"
        assert initial["documents"] == {
            "used": 0,
            "limit": 5,
            "remaining": 5,
            "percent": 0.0,
        }
        assert initial["answers"]["limit"] == 100

        documents = []
        for number in range(1, 6):
            uploaded = _upload(client, account, number)
            assert uploaded.status_code == 201, uploaded.text
            documents.append(uploaded.json())

        blocked = _upload(client, account, 6)
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "plan_limit_reached"
        assert "5 documents" in blocked.json()["detail"]["message"]
        usage = client.get("/v1/account/usage").json()
        assert usage["documents"]["used"] == 5
        assert usage["documents"]["remaining"] == 0

        deleted = client.delete(
            f"/v1/documents/{documents[0]['id']}",
            headers={"X-CSRF-Token": account["csrf_token"]},
        )
        assert deleted.status_code == 204
        assert _upload(client, account, 6).status_code == 201

        with app.state.repository.database.connect() as connection:
            connection.execute(
                "UPDATE workspaces SET plan = 'pro' WHERE id = ?",
                (account["workspace_id"],),
            )
        pro_usage = client.get("/v1/account/usage").json()
        assert pro_usage["plan"] == "pro"
        assert pro_usage["documents"]["limit"] == 100
        assert pro_usage["answers"]["limit"] == 2_000
        assert client.get("/v1/auth/me").json()["plan"] == "pro"


def test_monthly_answer_allowance_blocks_before_creating_history(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        account = _register(client)
        assert _upload(client, account, 1).status_code == 201
        usage_month = datetime.now(UTC).strftime("%Y-%m")
        with app.state.repository.database.connect() as connection:
            connection.execute(
                """INSERT INTO workspace_usage_monthly(
                       workspace_id, usage_month, answer_count, updated_at)
                   VALUES (?, ?, 99, ?)""",
                (account["workspace_id"], usage_month, datetime.now(UTC).isoformat()),
            )

        headers = {
            "X-CSRF-Token": account["csrf_token"],
            "Content-Type": "application/json",
        }
        allowed = client.post(
            "/v1/answers",
            headers=headers,
            json={"question": "How many annual leave days are provided?"},
        )
        assert allowed.status_code == 200
        before = len(client.get("/v1/conversations").json())

        blocked = client.post(
            "/v1/answers",
            headers=headers,
            json={"question": "What does the policy say?"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "plan_limit_reached"
        assert "100 questions this month" in blocked.json()["detail"]["message"]
        assert len(client.get("/v1/conversations").json()) == before
        assert client.get("/v1/account/usage").json()["answers"]["used"] == 100

        with app.state.repository.database.connect() as connection:
            connection.execute(
                "UPDATE workspaces SET plan = 'pro' WHERE id = ?",
                (account["workspace_id"],),
            )
        upgraded = client.post(
            "/v1/answers",
            headers=headers,
            json={"question": "What does the policy require?"},
        )
        assert upgraded.status_code == 200
        assert client.get("/v1/account/usage").json()["answers"]["used"] == 101


def test_pending_ingestion_jobs_hold_document_slots(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        account = _register(client)
        for number in range(1, 5):
            assert _upload(client, account, number).status_code == 201
        queued = client.post(
            "/v1/ingestion-jobs",
            headers={"X-CSRF-Token": account["csrf_token"]},
            files={
                "file": (
                    "queued-policy.txt",
                    b"A unique queued synthetic policy contains enough words for ingestion.",
                    "text/plain",
                )
            },
            data={"classification": "internal"},
        )
        assert queued.status_code == 202
        blocked = client.post(
            "/v1/ingestion-jobs",
            headers={"X-CSRF-Token": account["csrf_token"]},
            files={
                "file": (
                    "blocked-policy.txt",
                    b"Another unique synthetic policy should exceed the free document allowance.",
                    "text/plain",
                )
            },
            data={"classification": "internal"},
        )
        assert blocked.status_code == 429
        usage = client.get("/v1/account/usage").json()
        assert usage["stored_documents"] == 4
        assert usage["pending_documents"] == 1
        assert usage["documents"]["used"] == 5


def test_monthly_answer_reservations_are_atomic_under_concurrency(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        account = _register(client)

        def reserve() -> bool:
            return app.state.repository.reserve_answer_allowance(
                account["workspace_id"], 10
            )

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda _: reserve(), range(30)))

        assert results.count(True) == 10
        assert results.count(False) == 20
        usage = client.get("/v1/account/usage").json()
        assert usage["answers"]["used"] == 10
        assert usage["answers"]["remaining"] == 90
