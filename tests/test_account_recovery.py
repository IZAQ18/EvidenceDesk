from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.accounts import ACCOUNT_COOKIE
from app.config import Settings
from app.main import create_app


class CapturingEmailSender:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.verifications: list[tuple[str, str, str]] = []
        self.resets: list[tuple[str, str, str]] = []

    def send_verification(self, recipient: str, display_name: str, url: str) -> None:
        if self.fail:
            raise RuntimeError("synthetic delivery failure")
        self.verifications.append((recipient, display_name, url))

    def send_password_reset(self, recipient: str, display_name: str, url: str) -> None:
        if self.fail:
            raise RuntimeError("synthetic delivery failure")
        self.resets.append((recipient, display_name, url))


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "recovery.db",
        document_store_path=tmp_path / "originals",
        accounts_enabled=True,
        account_registration_enabled=True,
        account_email_verification_required=True,
        account_password_reset_enabled=True,
        public_base_url="https://accounts.example.test",
        rate_limit_per_minute=10_000,
    )


def _token(url: str) -> str:
    return parse_qs(urlsplit(url).query)["token"][0]


def _register(client: TestClient, email: str = "owner@example.com"):
    return client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "display_name": "Recovery Owner",
            "password": "original-correct-horse-password",
        },
    )


def test_email_verification_is_one_time_and_required_before_login(tmp_path):
    sender = CapturingEmailSender()
    app = create_app(_settings(tmp_path), account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        signin = client.get("/signin")
        assert "verify your email" in signin.text
        assert 'href="/forgot-password"' in signin.text
        hostile_page = client.get(
            "/verify-email", params={"token": "</script><script>alert(1)</script>"}
        )
        assert "</script><script>alert(1)</script>" not in hostile_page.text

        registered = _register(client)
        assert registered.status_code == 202
        assert registered.json()["action_required"] == "verify_email"
        assert ACCOUNT_COOKIE not in client.cookies
        assert len(sender.verifications) == 1
        recipient, _, url = sender.verifications[0]
        assert recipient == "owner@example.com"
        assert url.startswith("https://accounts.example.test/verify-email?token=")
        token = _token(url)

        blocked = client.post(
            "/v1/auth/login",
            json={"email": "owner@example.com", "password": "original-correct-horse-password"},
        )
        assert blocked.status_code == 401

        with app.state.repository.database.connect() as connection:
            user = connection.execute(
                "SELECT status, email_verified_at FROM users WHERE email = ?",
                ("owner@example.com",),
            ).fetchone()
            stored_token = connection.execute(
                "SELECT token_hash, used_at FROM account_tokens"
            ).fetchone()
        assert user["status"] == "pending_verification"
        assert user["email_verified_at"] is None
        assert stored_token["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in stored_token["token_hash"]

        page = client.get(f"/verify-email?token={token}")
        assert page.status_code == 200
        with app.state.repository.database.connect() as connection:
            assert connection.execute(
                "SELECT used_at FROM account_tokens"
            ).fetchone()["used_at"] is None

        with app.state.repository.database.connect() as connection:
            connection.execute(
                "UPDATE account_tokens SET expires_at = ? WHERE token_hash = ?",
                ("2000-01-01T00:00:00+00:00", hashlib.sha256(token.encode()).hexdigest()),
            )
        expired = client.post("/v1/auth/verify-email", json={"token": token})
        assert expired.status_code == 400
        resent = client.post(
            "/v1/auth/verification/resend", json={"email": "owner@example.com"}
        )
        assert resent.status_code == 202
        assert len(sender.verifications) == 2
        token = _token(sender.verifications[-1][2])

        verified = client.post("/v1/auth/verify-email", json={"token": token})
        assert verified.status_code == 200
        assert verified.json()["email"] == "owner@example.com"
        assert ACCOUNT_COOKIE in client.cookies
        assert client.get("/v1/auth/me").status_code == 200

        replay = client.post("/v1/auth/verify-email", json={"token": token})
        assert replay.status_code == 400
        with app.state.repository.database.connect() as connection:
            user = connection.execute(
                "SELECT status, email_verified_at FROM users WHERE email = ?",
                ("owner@example.com",),
            ).fetchone()
        assert user["status"] == "active"
        assert user["email_verified_at"]


def test_password_reset_is_private_one_time_and_revokes_sessions(tmp_path):
    sender = CapturingEmailSender()
    app = create_app(_settings(tmp_path), account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        _register(client)
        verification_token = _token(sender.verifications[-1][2])
        verified = client.post(
            "/v1/auth/verify-email", json={"token": verification_token}
        )
        assert verified.status_code == 200
        assert client.get("/v1/auth/me").status_code == 200

        existing = client.post(
            "/v1/auth/password-reset/request", json={"email": "owner@example.com"}
        )
        rotated = client.post(
            "/v1/auth/password-reset/request", json={"email": "owner@example.com"}
        )
        unknown = client.post(
            "/v1/auth/password-reset/request", json={"email": "unknown@example.com"}
        )
        assert existing.status_code == rotated.status_code == unknown.status_code == 202
        assert existing.json() == rotated.json() == unknown.json()
        assert len(sender.resets) == 2
        stale_token = _token(sender.resets[0][2])
        reset_token = _token(sender.resets[1][2])
        stale = client.post(
            "/v1/auth/password-reset/complete",
            json={"token": stale_token, "password": "stale-correct-horse-password"},
        )
        assert stale.status_code == 400

        reset_page = client.get(f"/reset-password?token={reset_token}")
        assert reset_page.status_code == 200
        assert "sign out every existing session" in reset_page.text
        completed = client.post(
            "/v1/auth/password-reset/complete",
            json={"token": reset_token, "password": "new-correct-horse-password"},
        )
        assert completed.status_code == 200
        assert completed.json()["action_required"] == "sign_in"
        assert ACCOUNT_COOKIE not in client.cookies
        assert client.get("/v1/auth/me").status_code == 401

        replay = client.post(
            "/v1/auth/password-reset/complete",
            json={"token": reset_token, "password": "another-correct-password"},
        )
        assert replay.status_code == 400
        old_login = client.post(
            "/v1/auth/login",
            json={"email": "owner@example.com", "password": "original-correct-horse-password"},
        )
        assert old_login.status_code == 401
        new_login = client.post(
            "/v1/auth/login",
            json={"email": "owner@example.com", "password": "new-correct-horse-password"},
        )
        assert new_login.status_code == 200


def test_account_email_requests_are_generic_when_delivery_fails(tmp_path):
    sender = CapturingEmailSender(fail=True)
    app = create_app(_settings(tmp_path), account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        registered = _register(client)
        assert registered.status_code == 202
        assert registered.json()["accepted"] is True
        unknown = client.post(
            "/v1/auth/verification/resend", json={"email": "unknown@example.com"}
        )
        assert unknown.status_code == 202


def test_production_account_email_requires_smtp_and_https(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"env": "production"})
    with pytest.raises(RuntimeError, match="SMTP delivery"):
        with TestClient(create_app(settings), base_url="https://testserver"):
            pass

    settings = settings.model_copy(
        update={
            "email_delivery_backend": "smtp",
            "smtp_host": "smtp.example.test",
            "smtp_from_address": "EvidenceDesk <accounts@example.test>",
            "public_base_url": "http://accounts.example.test",
        }
    )
    with pytest.raises(RuntimeError, match="HTTPS public base URL"):
        with TestClient(create_app(settings), base_url="https://testserver"):
            pass

    settings = settings.model_copy(
        update={"public_base_url": "https://accounts.example.test", "smtp_starttls": False}
    )
    with pytest.raises(RuntimeError, match="STARTTLS"):
        with TestClient(create_app(settings), base_url="https://testserver"):
            pass
