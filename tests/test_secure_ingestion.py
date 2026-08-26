from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.config import PrincipalConfig, Settings
from app.isolated_extraction import _runner_environment, extract_in_isolated_process
from app.main import create_app
from app.malware import ScanResult
from tests.conftest import auth


class InfectedScanner:
    name = "test-clamav"

    def scan(self, content: bytes) -> ScanResult:
        assert content
        return ScanResult(status="infected", engine=self.name)

    def ping(self) -> bool:
        return True


class UnavailableScanner:
    name = "test-clamav"

    def scan(self, content: bytes) -> ScanResult:
        del content
        raise ConnectionError("synthetic scanner outage")

    def ping(self) -> bool:
        return True


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "env": "test",
        "db_path": tmp_path / "secure-ingestion.db",
        "document_store_path": tmp_path / "originals",
        "rate_limit_per_minute": 10_000,
        "malware_scanner_backend": "clamd",
        "malware_scan_required": True,
        "api_keys": {
            "admin-a": PrincipalConfig(
                subject="admin-a",
                tenant_id="tenant-a",
                role="admin",
                clearances=["public", "internal", "confidential"],
            )
        },
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _wait(client: TestClient, job_id: str, stage: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/v1/ingestion-jobs/{job_id}", headers=auth()).json()
        if job["stage"] == stage:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not reach {stage}")


def test_malware_is_quarantined_never_indexed_and_visible_to_admin(tmp_path):
    app = create_app(_settings(tmp_path), malware_scanner=InfectedScanner())
    with TestClient(app) as client:
        response = client.post(
            "/v1/ingestion-jobs",
            headers=auth(),
            files={
                "file": (
                    "synthetic-eicar-like.txt",
                    b"Synthetic antivirus test material with enough words for upload validation.",
                    "text/plain",
                )
            },
        )
        assert response.status_code == 202
        job = _wait(client, response.json()["id"], "quarantined")
        assert job["status"] == "failed"
        assert job["error_code"] == "malware_detected"
        assert "storage_path" not in job
        assert client.get("/v1/documents", headers=auth()).json() == []
        assert client.post(f"/v1/ingestion-jobs/{job['id']}/retry", headers=auth()).status_code == 409

        metrics = client.get("/v1/admin/metrics", headers=auth()).json()
        assert metrics["ingestion"]["quarantined"] == 1
        assert metrics["ingestion"]["dead_letter"] == 0

        assert client.delete(f"/v1/ingestion-jobs/{job['id']}", headers=auth()).status_code == 204
        assert not list((tmp_path / "originals" / "quarantine").rglob("*.txt"))


def test_scanner_outage_retries_then_enters_dead_letter(tmp_path):
    settings = _settings(tmp_path, ingestion_job_max_attempts=2)
    app = create_app(settings, malware_scanner=UnavailableScanner())
    with TestClient(app) as client:
        response = client.post(
            "/v1/ingestion-jobs",
            headers=auth(),
            files={
                "file": (
                    "scanner-outage.txt",
                    b"A synthetic document containing enough harmless words for scanner outage testing.",
                    "text/plain",
                )
            },
        )
        first = _wait(client, response.json()["id"], "failed")
        assert first["error_code"] == "malware_scanner_unavailable"
        retried = client.post(f"/v1/ingestion-jobs/{first['id']}/retry", headers=auth())
        assert retried.status_code == 202
        exhausted = _wait(client, first["id"], "dead_letter")
        assert exhausted["attempts"] == 2
        assert client.post(f"/v1/ingestion-jobs/{first['id']}/retry", headers=auth()).status_code == 409
        metrics = client.get("/v1/admin/metrics", headers=auth()).json()
        assert metrics["ingestion"]["dead_letter"] == 1


def test_synchronous_compatibility_upload_is_also_scanned(tmp_path):
    app = create_app(_settings(tmp_path), malware_scanner=InfectedScanner())
    with TestClient(app) as client:
        response = client.post(
            "/v1/documents",
            headers=auth(),
            files={
                "file": (
                    "blocked.txt",
                    b"Synthetic content that must be rejected before synchronous document storage.",
                    "text/plain",
                )
            },
        )
        assert response.status_code == 422
        assert "malware" in response.json()["detail"].lower()
        assert client.get("/v1/documents", headers=auth()).json() == []


def test_extraction_process_is_bounded_and_does_not_inherit_application_secrets(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EVIDENCEDESK_MODEL_API_KEY", "must-not-enter-parser")
    assert "EVIDENCEDESK_MODEL_API_KEY" not in _runner_environment()
    settings = Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "unused.db",
        ingestion_process_isolation_enabled=True,
        ingestion_extraction_timeout_seconds=30,
        ingestion_worker_memory_mb=512,
    )
    content = b"This isolated extraction document contains enough words to pass every text check."
    extracted = extract_in_isolated_process("isolated.txt", "text/plain", content, settings)
    assert extracted.text == content.decode()
    assert extracted.extraction_method == "plain-text"
