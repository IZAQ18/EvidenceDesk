from __future__ import annotations

import pytest

from app.config import Settings
from app.models import Principal
from app.security import authenticate_key
from app.storage import DocumentStorage
from tests.conftest import auth, ingest


def test_original_and_page_follow_classification_policy(client):
    confidential = ingest(
        client,
        "The confidential incident response passphrase is VIOLET-EMBER for this test only.",
        classification="confidential",
    ).json()

    original = client.get(
        f"/v1/documents/{confidential['id']}/content", headers=auth("reader-a")
    )
    page = client.get(
        f"/v1/documents/{confidential['id']}/pages/1", headers=auth("reader-a")
    )
    assert original.status_code == 404
    assert page.status_code == 404
    assert "VIOLET-EMBER" not in original.text
    assert "VIOLET-EMBER" not in page.text


def test_conversations_feedback_and_reports_are_owner_isolated(client):
    ingest(
        client,
        "Employees must report a suspected security incident to the response team immediately.",
    )
    answer = client.post(
        "/v1/answers",
        headers=auth(),
        json={"question": "Where should employees report a security incident?"},
    ).json()

    messages = client.get(
        f"/v1/conversations/{answer['conversation_id']}/messages",
        headers=auth("admin-b"),
    )
    report = client.get(
        f"/v1/messages/{answer['message_id']}/report", headers=auth("admin-b")
    )
    feedback = client.post(
        "/v1/feedback",
        headers=auth("admin-b"),
        json={"message_id": answer["message_id"], "category": "helpful"},
    )
    assert messages.status_code == 404
    assert report.status_code == 404
    assert feedback.status_code == 404


def test_admin_surfaces_reject_reader_role(client):
    metrics = client.get("/v1/admin/metrics", headers=auth("reader-a"))
    bulk = client.post(
        "/v1/documents/bulk",
        headers=auth("reader-a"),
        json={"document_ids": ["not-a-document"], "action": "archive"},
    )
    assert metrics.status_code == 403
    assert bulk.status_code == 403


def test_storage_rejects_paths_outside_generated_root(tmp_path):
    storage = DocumentStorage(tmp_path / "originals")
    with pytest.raises(ValueError, match="Unsafe document storage path"):
        storage.path_for("../private.env")


def test_optional_oidc_adapter_maps_validated_identity(monkeypatch, tmp_path):
    settings = Settings(
        env="test",
        auth_mode="hybrid",
        db_path=tmp_path / "test.db",
        oidc_issuer="https://identity.example.test/",
        oidc_audience="evidencedesk-api",
        oidc_jwks_url="https://identity.example.test/jwks.json",
    )
    expected = Principal(
        subject="oidc-user",
        tenant_id="tenant-a",
        role="reader",
        clearances=["public", "internal"],
    )
    monkeypatch.setattr("app.security._decode_oidc_token", lambda token, config: expected)

    principal = authenticate_key(
        x_api_key=None,
        authorization="Bearer signed-test-token",
        settings=settings,
    )

    assert principal == expected


def test_oidc_mode_fails_fast_when_provider_configuration_is_missing(tmp_path):
    settings = Settings(env="test", auth_mode="oidc", db_path=tmp_path / "test.db")
    with pytest.raises(RuntimeError, match="OIDC configuration is incomplete"):
        settings.validate_auth()
