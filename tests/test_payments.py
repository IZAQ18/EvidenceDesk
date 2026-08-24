from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.config import PrincipalConfig, Settings
from app.main import create_app


def _payload() -> bytes:
    return json.dumps(
        {
            "meta": {"event_name": "order_created"},
            "data": {
                "type": "orders",
                "id": "order-42",
                "attributes": {
                    "status": "paid",
                    "total": 2500,
                    "currency": "USD",
                    "user_email": "buyer@example.com",
                    "test_mode": False,
                },
            },
        },
        separators=(",", ":"),
    ).encode()


def _signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signed_payment_event_notifies_admin_without_banking_data(tmp_path):
    secret = "test-webhook-secret"
    settings = Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "payments.db",
        document_store_path=tmp_path / "originals",
        pilot_tenant_id="pilot",
        payment_webhook_secret=secret,
        api_keys={
            "payments-admin": PrincipalConfig(
                subject="owner",
                tenant_id="pilot",
                role="admin",
                clearances=["public", "internal", "confidential"],
            )
        },
    )
    body = _payload()
    headers = {"Content-Type": "application/json", "X-Signature": _signature(body, secret)}

    with TestClient(create_app(settings)) as client:
        accepted = client.post("/v1/payments/webhook", content=body, headers=headers)
        duplicate = client.post("/v1/payments/webhook", content=body, headers=headers)
        assert accepted.json() == {"accepted": True, "duplicate": False}
        assert duplicate.json() == {"accepted": True, "duplicate": True}

        metrics = client.get(
            "/v1/admin/metrics", headers={"X-API-Key": "payments-admin"}
        ).json()
        assert metrics["payments"]["totals"] == [
            {"currency": "USD", "amount_minor": 2500, "payment_count": 1}
        ]
        assert len(metrics["payments"]["recent"]) == 1
        event = metrics["payments"]["recent"][0]
        assert event["customer_hint"] == "bu***@example.com"
        assert "buyer@example.com" not in json.dumps(metrics)
        assert "account" not in json.dumps(event).lower()


def test_payment_webhook_rejects_invalid_signature_and_missing_configuration(tmp_path):
    body = _payload()
    configured = Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "configured.db",
        document_store_path=tmp_path / "configured-originals",
        payment_webhook_secret="correct-secret",
    )
    with TestClient(create_app(configured)) as client:
        rejected = client.post(
            "/v1/payments/webhook",
            content=body,
            headers={"X-Signature": "wrong-signature"},
        )
        assert rejected.status_code == 401
        assert rejected.json()["detail"] == "Invalid payment event"

    disabled = Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "disabled.db",
        document_store_path=tmp_path / "disabled-originals",
    )
    with TestClient(create_app(disabled)) as client:
        assert client.post("/v1/payments/webhook", content=body).status_code == 404
