from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

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


def _subscription_payload(
    *,
    workspace_id: str,
    entitlement_token: str,
    status: str = "active",
    variant_id: int = 9001,
    updated_at: str = "2026-08-26T10:00:00Z",
    test_mode: bool = False,
    provider_id: str = "subscription-42",
) -> bytes:
    return json.dumps(
        {
            "meta": {
                "event_name": "subscription_updated",
                "custom_data": {
                    "workspace_id": workspace_id,
                    "entitlement_token": entitlement_token,
                },
            },
            "data": {
                "type": "subscriptions",
                "id": provider_id,
                "attributes": {
                    "status": status,
                    "variant_id": variant_id,
                    "currency": "USD",
                    "user_email": "buyer@example.com",
                    "updated_at": updated_at,
                    "test_mode": test_mode,
                },
            },
        },
        separators=(",", ":"),
    ).encode()


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
        assert accepted.json() == {
            "accepted": True,
            "duplicate": False,
            "entitlement_eligible": False,
            "entitlement_updated": False,
        }
        assert duplicate.json() == {
            "accepted": True,
            "duplicate": True,
            "entitlement_eligible": False,
            "entitlement_updated": False,
        }

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


def test_pro_checkout_and_webhooks_update_only_the_bound_workspace(tmp_path):
    secret = "a-server-only-webhook-secret"
    settings = Settings(
        _env_file=None,
        env="production",
        db_path=tmp_path / "entitlements.db",
        document_store_path=tmp_path / "originals",
        accounts_enabled=True,
        account_registration_enabled=True,
        payment_webhook_secret=secret,
        payment_entitlements_enabled=True,
        pro_checkout_url="https://store.example/checkout/buy/pro-variant",
        payment_pro_variant_ids="9001",
        rate_limit_per_minute=10_000,
    )
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as client:
        registered = client.post(
            "/v1/auth/register",
            json={
                "email": "owner@example.com",
                "display_name": "Workspace Owner",
                "password": "a-correct-horse-battery-staple",
            },
        )
        assert registered.status_code == 201
        account = registered.json()
        workspace_page = client.get("/")
        assert "const PRO_CHECKOUT_ENABLED=true" in workspace_page.text
        assert "__PRO_CHECKOUT_ENABLED__" not in workspace_page.text

        checkout = client.get("/v1/billing/checkout", follow_redirects=False)
        assert checkout.status_code == 303
        query = parse_qs(urlsplit(checkout.headers["location"]).query)
        assert query["checkout[custom][workspace_id]"] == [account["workspace_id"]]
        token = query["checkout[custom][entitlement_token]"][0]
        assert len(token) == 64
        assert secret not in checkout.headers["location"]

        active = _subscription_payload(
            workspace_id=account["workspace_id"], entitlement_token=token
        )
        accepted = client.post(
            "/v1/payments/webhook",
            content=active,
            headers={"X-Signature": _signature(active, secret)},
        )
        assert accepted.json() == {
            "accepted": True,
            "duplicate": False,
            "entitlement_eligible": True,
            "entitlement_updated": True,
        }
        assert client.get("/v1/auth/me").json()["plan"] == "pro"
        assert client.get("/v1/billing/checkout").status_code == 409

        duplicate = client.post(
            "/v1/payments/webhook",
            content=active,
            headers={"X-Signature": _signature(active, secret)},
        )
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["entitlement_updated"] is False

        stale = _subscription_payload(
            workspace_id=account["workspace_id"],
            entitlement_token=token,
            status="expired",
            updated_at="2026-08-25T10:00:00Z",
        )
        stale_result = client.post(
            "/v1/payments/webhook",
            content=stale,
            headers={"X-Signature": _signature(stale, secret)},
        )
        assert stale_result.json()["entitlement_eligible"] is True
        assert stale_result.json()["entitlement_updated"] is False
        assert client.get("/v1/auth/me").json()["plan"] == "pro"

        expired = _subscription_payload(
            workspace_id=account["workspace_id"],
            entitlement_token=token,
            status="expired",
            updated_at="2026-08-27T10:00:00Z",
        )
        expired_result = client.post(
            "/v1/payments/webhook",
            content=expired,
            headers={"X-Signature": _signature(expired, secret)},
        )
        assert expired_result.json()["entitlement_updated"] is True
        assert client.get("/v1/auth/me").json()["plan"] == "free"

        test_mode = _subscription_payload(
            workspace_id=account["workspace_id"],
            entitlement_token=token,
            updated_at="2026-08-28T10:00:00Z",
            test_mode=True,
        )
        test_result = client.post(
            "/v1/payments/webhook",
            content=test_mode,
            headers={"X-Signature": _signature(test_mode, secret)},
        )
        assert test_result.json()["entitlement_eligible"] is False
        assert client.get("/v1/auth/me").json()["plan"] == "free"

        renewed = _subscription_payload(
            workspace_id=account["workspace_id"],
            entitlement_token=token,
            provider_id="subscription-43",
            updated_at="2026-08-29T10:00:00Z",
        )
        renewed_result = client.post(
            "/v1/payments/webhook",
            content=renewed,
            headers={"X-Signature": _signature(renewed, secret)},
        )
        assert renewed_result.json()["entitlement_updated"] is True
        assert client.get("/v1/auth/me").json()["plan"] == "pro"

        old_subscription_finished = _subscription_payload(
            workspace_id=account["workspace_id"],
            entitlement_token=token,
            status="expired",
            updated_at="2026-08-30T10:00:00Z",
        )
        old_result = client.post(
            "/v1/payments/webhook",
            content=old_subscription_finished,
            headers={"X-Signature": _signature(old_subscription_finished, secret)},
        )
        assert old_result.json()["entitlement_updated"] is True
        assert client.get("/v1/auth/me").json()["plan"] == "pro"

        renewed_finished = _subscription_payload(
            workspace_id=account["workspace_id"],
            entitlement_token=token,
            provider_id="subscription-43",
            status="expired",
            updated_at="2026-08-31T10:00:00Z",
        )
        renewed_finished_result = client.post(
            "/v1/payments/webhook",
            content=renewed_finished,
            headers={"X-Signature": _signature(renewed_finished, secret)},
        )
        assert renewed_finished_result.json()["entitlement_updated"] is True
        assert client.get("/v1/auth/me").json()["plan"] == "free"

        with app.state.repository.database.connect() as connection:
            stored = connection.execute(
                "SELECT provider, provider_id, variant_id, status, plan "
                "FROM workspace_entitlements WHERE workspace_id = ? ORDER BY provider_id",
                (account["workspace_id"],),
            ).fetchall()
        assert [dict(row) for row in stored] == [
            {
                "provider": "lemonsqueezy",
                "provider_id": "subscription-42",
                "variant_id": "9001",
                "status": "expired",
                "plan": "free",
            },
            {
                "provider": "lemonsqueezy",
                "provider_id": "subscription-43",
                "variant_id": "9001",
                "status": "expired",
                "plan": "free",
            },
        ]


def test_entitlements_fail_closed_when_configuration_is_incomplete(tmp_path):
    with pytest.raises(ValidationError, match="require account authentication"):
        Settings(
            _env_file=None,
            env="test",
            db_path=tmp_path / "invalid.db",
            payment_entitlements_enabled=True,
            payment_webhook_secret="secret",
            pro_checkout_url="https://store.example/checkout/buy/pro",
            payment_pro_variant_ids="9001",
        )
