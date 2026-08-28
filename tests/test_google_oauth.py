from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import Settings
from app.google_oauth import (
    GOOGLE_OAUTH_COOKIE,
    GoogleIdentity,
    GoogleOAuthClient,
    GoogleOAuthError,
    GoogleOAuthResult,
)
from app.main import create_app


class FakeGoogleOAuth:
    enabled = True

    def __init__(self, identity: GoogleIdentity | None = None):
        self.identity = identity or GoogleIdentity(
            subject="google-subject-123",
            email="person@example.com",
            display_name="Google Person",
        )
        self.completed: tuple[str, str, str] | None = None

    def begin(self, next_path: str) -> tuple[str, str]:
        return (
            "https://accounts.google.test/authorize?state=test-state",
            f"signed-attempt:{next_path}",
        )

    async def complete(self, code: str, state: str, attempt_cookie: str) -> GoogleOAuthResult:
        self.completed = (code, state, attempt_cookie)
        if state != "test-state" or not attempt_cookie.startswith("signed-attempt:"):
            raise GoogleOAuthError("state_invalid")
        return GoogleOAuthResult(
            self.identity,
            attempt_cookie.removeprefix("signed-attempt:"),
        )


def _settings(tmp_path, *, registration: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        env="production",
        db_path=tmp_path / "google.db",
        document_store_path=tmp_path / "originals",
        accounts_enabled=True,
        account_registration_enabled=registration,
        google_oauth_enabled=True,
        google_oauth_registration_enabled=registration,
        google_oauth_client_id="test-client.apps.googleusercontent.com",
        google_oauth_client_secret="server-only-test-secret",
        public_base_url="https://testserver",
        account_session_hours=24,
        rate_limit_per_minute=10_000,
    )


def test_google_signin_creates_private_account_session_and_stable_identity(tmp_path):
    provider = FakeGoogleOAuth()
    app = create_app(_settings(tmp_path), google_oauth_provider=provider)
    with TestClient(app, base_url="https://testserver") as client:
        signin = client.get("/signin?next=%2Fplans")
        assert "Continue with Google" in signin.text
        assert 'class="google-button" href="/auth/google/start?next=%2Fplans"' in signin.text
        assert signin.text.count("<!doctype html>") == 1
        assert "Sessions use secure HttpOnly cookies" not in signin.text
        assert "never receives your Google password" in signin.text

        start = client.get("/auth/google/start?next=%2Fplans", follow_redirects=False)
        assert start.status_code == 303
        assert start.headers["location"].startswith("https://accounts.google.test/")
        oauth_cookie = start.headers["set-cookie"]
        assert GOOGLE_OAUTH_COOKIE in oauth_cookie
        assert "HttpOnly" in oauth_cookie
        assert "Secure" in oauth_cookie
        assert "SameSite=lax" in oauth_cookie

        callback = client.get(
            "/auth/google/callback?code=one-time-code&state=test-state",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/plans"
        assert "evidencedesk_session=" in callback.headers["set-cookie"]
        assert provider.completed == (
            "one-time-code",
            "test-state",
            "signed-attempt:/plans",
        )

        account = client.get("/v1/auth/me").json()
        assert account["email"] == "person@example.com"
        assert account["display_name"] == "Google Person"
        assert account["role"] == "admin"
        first_user = account["id"]
        first_workspace = account["workspace_id"]

        with app.state.repository.database.connect() as connection:
            identity = connection.execute(
                "SELECT provider, subject, user_id FROM account_identities"
            ).fetchone()
            user = connection.execute(
                "SELECT email_verified_at, password_hash FROM users WHERE id = ?",
                (first_user,),
            ).fetchone()
        assert dict(identity) == {
            "provider": "google",
            "subject": "google-subject-123",
            "user_id": first_user,
        }
        assert user["email_verified_at"]
        assert user["password_hash"].startswith("scrypt$")

        client.get("/auth/google/start", follow_redirects=False)
        second = client.get(
            "/auth/google/callback?code=second-code&state=test-state",
            follow_redirects=False,
        )
        assert second.status_code == 303
        again = client.get("/v1/auth/me").json()
        assert again["id"] == first_user
        assert again["workspace_id"] == first_workspace


def test_google_signin_links_verified_email_and_respects_invite_only(tmp_path):
    settings = _settings(tmp_path, registration=False)
    existing = FakeGoogleOAuth(
        GoogleIdentity("existing-google-sub", "member@example.com", "Member")
    )
    app = create_app(settings, google_oauth_provider=existing)
    with TestClient(app, base_url="https://testserver") as client:
        app.state.account_store.register(
            "member@example.com", "Existing Member", "a-correct-horse-battery-staple"
        )
        client.get("/auth/google/start", follow_redirects=False)
        linked = client.get(
            "/auth/google/callback?code=link-code&state=test-state",
            follow_redirects=False,
        )
        assert linked.status_code == 303
        assert client.get("/v1/auth/me").json()["display_name"] == "Existing Member"

    unknown = FakeGoogleOAuth(
        GoogleIdentity("unknown-google-sub", "unknown@example.com", "Unknown")
    )
    blocked_app = create_app(
        _settings(tmp_path / "blocked", registration=False),
        google_oauth_provider=unknown,
    )
    with TestClient(blocked_app, base_url="https://testserver") as client:
        client.get("/auth/google/start", follow_redirects=False)
        blocked = client.get(
            "/auth/google/callback?code=blocked-code&state=test-state",
            follow_redirects=False,
        )
        assert blocked.status_code == 303
        assert blocked.headers["location"] == "/signin?auth_error=registration_closed"
        page = client.get(blocked.headers["location"])
        assert "not currently invited" in page.text


def test_google_registration_can_be_public_while_password_registration_stays_closed(tmp_path):
    settings = _settings(tmp_path, registration=True)
    settings.account_registration_enabled = False
    app = create_app(settings, google_oauth_provider=FakeGoogleOAuth())
    with TestClient(app, base_url="https://testserver") as client:
        signin = client.get("/signin")
        assert "Continue with Google to create a private workspace" in signin.text
        assert "Create account" not in signin.text
        client.get("/auth/google/start", follow_redirects=False)
        callback = client.get(
            "/auth/google/callback?code=google-only&state=test-state",
            follow_redirects=False,
        )
        assert callback.status_code == 303
        assert client.get("/v1/auth/me").json()["email"] == "person@example.com"


def test_google_callback_rejects_missing_state_and_maps_provider_denial(tmp_path):
    app = create_app(_settings(tmp_path), google_oauth_provider=FakeGoogleOAuth())
    with TestClient(app, base_url="https://testserver") as client:
        rejected = client.get(
            "/auth/google/callback?code=code&state=wrong",
            follow_redirects=False,
        )
        assert rejected.headers["location"] == "/signin?auth_error=state_invalid"
        denied = client.get(
            "/auth/google/callback?error=access_denied",
            follow_redirects=False,
        )
        assert denied.headers["location"] == "/signin?auth_error=google_denied"


def test_real_google_client_builds_minimal_scoped_signed_request_and_validates_nonce(
    monkeypatch,
):
    settings = Settings(
        _env_file=None,
        env="production",
        accounts_enabled=True,
        account_registration_enabled=True,
        google_oauth_enabled=True,
        google_oauth_client_id="test-client.apps.googleusercontent.com",
        google_oauth_client_secret="server-only-google-secret",
        public_base_url="https://app.example.com",
    )
    settings.validate_auth()
    client = GoogleOAuthClient(settings)
    authorization_url, attempt_cookie = client.begin("//evil.example/path")
    query = parse_qs(urlsplit(authorization_url).query)
    assert query["scope"] == ["openid email profile"]
    assert query["redirect_uri"] == ["https://app.example.com/auth/google/callback"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert "server-only-google-secret" not in authorization_url
    assert "server-only-google-secret" not in attempt_cookie

    attempt = client._decode_attempt(attempt_cookie)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeKey:
        key = private_key.public_key()

    class FakeJwks:
        def get_signing_key_from_jwt(self, encoded):
            return FakeKey()

    monkeypatch.setattr("app.google_oauth._google_jwks_client", lambda _: FakeJwks())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": settings.google_oauth_client_id,
        "sub": "stable-google-subject",
        "email": "verified@example.com",
        "email_verified": True,
        "name": "Verified Person",
        "nonce": attempt["nonce"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    encoded = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})
    identity = client._verify_id_token(encoded, str(attempt["nonce"]))
    assert identity.email == "verified@example.com"
    with pytest.raises(GoogleOAuthError, match="identity_invalid"):
        client._verify_id_token(encoded, "wrong-nonce")


def test_google_oauth_configuration_fails_closed_without_required_secrets(tmp_path):
    settings = Settings(
        _env_file=None,
        env="production",
        db_path=tmp_path / "missing-google.db",
        accounts_enabled=True,
        google_oauth_enabled=True,
    )
    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_ID"):
        settings.validate_auth()
