from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.config import PrincipalConfig, Settings
from app.main import create_app
from tests.conftest import auth


def _wait_for_job(client: TestClient, job_id: str, terminal: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/v1/ingestion-jobs/{job_id}", headers=auth()).json()
        if job["status"] == terminal:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not reach {terminal}")


def test_async_job_reaches_ready_and_preserves_tenant_isolation(client):
    response = client.post(
        "/v1/ingestion-jobs",
        headers=auth(),
        files={
            "file": (
                "leave-policy.txt",
                b"Employees receive twenty paid leave days each calendar year. "
                b"Requests should be submitted to the manager before travel.",
                "text/plain",
            )
        },
        data={"classification": "internal", "source": "People Operations"},
    )
    assert response.status_code == 202
    queued = response.json()
    assert queued["status"] in {"queued", "processing"}
    assert queued["progress"] >= 15

    assert (
        client.get(f"/v1/ingestion-jobs/{queued['id']}", headers=auth("admin-b")).status_code
        == 404
    )
    ready = _wait_for_job(client, queued["id"], "ready")
    assert ready["stage"] == "ready"
    assert ready["progress"] == 100

    documents = client.get("/v1/documents", headers=auth()).json()
    document = next(item for item in documents if item["id"] == ready["document_id"])
    assert document["filename"] == "leave-policy.txt"
    assert document["has_original"] is True
    answer = client.post(
        "/v1/answers",
        headers=auth(),
        json={"question": "How many paid leave days do employees receive?"},
    ).json()
    assert answer["grounded"] is True
    assert any(citation["document_id"] == document["id"] for citation in answer["citations"])


def test_failed_job_can_retry_until_bound_then_delete_staged_file(client):
    response = client.post(
        "/v1/ingestion-jobs",
        headers=auth(),
        files={"file": ("empty-policy.txt", b"too short", "text/plain")},
        data={"classification": "internal"},
    )
    assert response.status_code == 202
    job_id = response.json()["id"]

    failed = _wait_for_job(client, job_id, "failed")
    assert failed["error_code"] == "extraction_rejected"
    assert failed["attempts"] == 1

    for expected_attempt in (2, 3):
        retried = client.post(
            f"/v1/ingestion-jobs/{job_id}/retry", headers=auth()
        )
        assert retried.status_code == 202
        failed = _wait_for_job(client, job_id, "failed")
        assert failed["attempts"] == expected_attempt

    exhausted = client.post(f"/v1/ingestion-jobs/{job_id}/retry", headers=auth())
    assert exhausted.status_code == 409
    assert client.delete(f"/v1/ingestion-jobs/{job_id}", headers=auth()).status_code == 204
    assert client.get(f"/v1/ingestion-jobs/{job_id}", headers=auth()).status_code == 404


def test_expired_worker_lease_is_claimed_again(tmp_path):
    settings = Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "lease.db",
        document_store_path=tmp_path / "originals",
        ingestion_worker_enabled=False,
        api_keys={
            "admin-a": PrincipalConfig(
                subject="admin-a",
                tenant_id="tenant-a",
                role="admin",
                clearances=["public", "internal", "confidential"],
            )
        },
    )
    app = create_app(settings)
    with TestClient(app) as client:
        queued = client.post(
            "/v1/ingestion-jobs",
            headers=auth(),
            files={
                "file": (
                    "policy.txt",
                    b"This durable policy contains enough words to pass the extraction safety checks.",
                    "text/plain",
                )
            },
        ).json()
        repository = app.state.repository
        first = repository.claim_ingestion_job("worker-one", lease_seconds=30)
        assert first and first["id"] == queued["id"]
        assert repository.claim_ingestion_job("worker-two", lease_seconds=30) is None
        with repository.database.connect() as connection:
            connection.execute(
                "UPDATE ingestion_jobs SET lease_expires_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", queued["id"]),
            )
        recovered = repository.claim_ingestion_job("worker-two", lease_seconds=30)
        assert recovered and recovered["id"] == queued["id"]
        assert recovered["attempts"] == 2
