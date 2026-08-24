from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import PrincipalConfig, Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "test.db",
        document_store_path=tmp_path / "originals",
        rate_limit_per_minute=10_000,
        api_keys={
            "admin-a": PrincipalConfig(
                subject="admin-a",
                tenant_id="tenant-a",
                role="admin",
                clearances=["public", "internal", "confidential"],
            ),
            "reader-a": PrincipalConfig(
                subject="reader-a",
                tenant_id="tenant-a",
                role="reader",
                clearances=["public", "internal"],
            ),
            "admin-b": PrincipalConfig(
                subject="admin-b",
                tenant_id="tenant-b",
                role="admin",
                clearances=["public", "internal", "confidential"],
            ),
        },
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def auth(key: str = "admin-a") -> dict[str, str]:
    return {"X-API-Key": key}


def ingest(client: TestClient, text: str, *, name: str = "policy.txt", classification: str = "internal", key: str = "admin-a"):
    return client.post(
        "/v1/documents",
        headers=auth(key),
        files={"file": (name, text.encode(), "text/plain")},
        data={"classification": classification, "source": "Test handbook"},
    )
