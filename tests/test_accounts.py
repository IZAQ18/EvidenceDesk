import pytest
from fastapi.testclient import TestClient

from app.accounts import ACCOUNT_COOKIE
from app.config import Settings
from app.main import create_app


def _account_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        env="production",
        db_path=tmp_path / "accounts.db",
        document_store_path=tmp_path / "originals",
        accounts_enabled=True,
        ephemeral_account_testing_enabled=True,
        account_registration_enabled=True,
        account_session_hours=24,
        rate_limit_per_minute=10_000,
    )


def _register(client: TestClient, email: str, name: str = "Ahsan Ahmad"):
    return client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "display_name": name,
            "password": "a-correct-horse-battery-staple",
        },
    )


def test_account_registration_session_csrf_and_private_workspace(tmp_path):
    app = create_app(_account_settings(tmp_path))
    with TestClient(app, base_url="https://testserver") as client:
        landing = client.get("/", follow_redirects=False)
        assert landing.status_code == 307
        assert landing.headers["location"] == "/signin"
        assert "Create account" in client.get("/signin").text
        assert 'href="/signin"' in client.get("/about").text

        anonymous_api = client.get("/v1/documents")
        assert anonymous_api.status_code == 401
        assert anonymous_api.json()["detail"] == "Sign in required"

        weak = client.post(
            "/v1/auth/register",
            json={"email": "owner@example.com", "display_name": "Owner", "password": "short"},
        )
        assert weak.status_code == 422

        registered = _register(client, " Owner@Example.com ")
        assert registered.status_code == 201
        owner = registered.json()
        assert owner["email"] == "owner@example.com"
        assert owner["role"] == "admin"
        assert owner["plan"] == "free"
        assert owner["workspace_id"]
        assert owner["csrf_token"]
        cookie = registered.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie

        workspace = client.get("/")
        assert workspace.status_code == 200
        assert "const ACCOUNT_MODE=true" in workspace.text
        assert "__ACCOUNT_MODE__" not in workspace.text

        capabilities = client.get("/v1/capabilities").json()
        assert capabilities["durability"] == {
            "profile": "ephemeral",
            "customer_data_ready": False,
            "synthetic_acceptance_only": True,
        }

        me = client.get("/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["workspace_name"] == "Ahsan Ahmad's workspace"

        rejected_upload = client.post(
            "/v1/documents",
            files={"file": ("policy.txt", b"Annual leave is 20 days.", "text/plain")},
            data={"classification": "internal"},
        )
        assert rejected_upload.status_code == 403
        assert rejected_upload.json()["detail"] == "Invalid session security token"

        uploaded = client.post(
            "/v1/documents",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            files={"file": ("policy.txt", b"Annual leave is 20 days.", "text/plain")},
            data={"classification": "internal"},
        )
        assert uploaded.status_code == 201
        assert len(client.get("/v1/documents").json()) == 1

        with app.state.repository.database.connect() as connection:
            stored = connection.execute(
                "SELECT email, password_hash FROM users WHERE id = ?", (owner["id"],)
            ).fetchone()
        assert stored["email"] == "owner@example.com"
        assert "correct-horse" not in stored["password_hash"]
        assert stored["password_hash"].startswith("scrypt$")


def test_accounts_are_isolated_and_sessions_can_be_revoked(tmp_path):
    app = create_app(_account_settings(tmp_path))
    with TestClient(app, base_url="https://testserver") as client:
        first = _register(client, "first@example.com", "First User").json()
        uploaded = client.post(
            "/v1/documents",
            headers={"X-CSRF-Token": first["csrf_token"]},
            files={
                "file": (
                    "private.txt",
                    b"First workspace confidential policy applies only to authorized employees.",
                    "text/plain",
                )
            },
            data={"classification": "confidential"},
        )
        assert uploaded.status_code == 201

        second_response = _register(client, "second@example.com", "Second User")
        assert second_response.status_code == 201
        second = second_response.json()
        assert second["workspace_id"] != first["workspace_id"]
        assert client.get("/v1/documents").json() == []

        duplicate = _register(client, "FIRST@example.com", "Duplicate")
        assert duplicate.status_code == 409

        invalid_login = client.post(
            "/v1/auth/login",
            json={"email": "first@example.com", "password": "a-completely-wrong-password"},
        )
        assert invalid_login.status_code == 401
        assert invalid_login.json()["detail"] == "Email or password is incorrect"

        first_login = client.post(
            "/v1/auth/login",
            json={
                "email": "first@example.com",
                "password": "a-correct-horse-battery-staple",
            },
        )
        assert first_login.status_code == 200
        first_again = first_login.json()
        assert len(client.get("/v1/documents").json()) == 1

        missing_csrf = client.post("/v1/auth/logout")
        assert missing_csrf.status_code == 403
        logged_out = client.post(
            "/v1/auth/logout", headers={"X-CSRF-Token": first_again["csrf_token"]}
        )
        assert logged_out.status_code == 204
        assert client.get("/v1/auth/me").status_code == 401

        client.cookies.set(ACCOUNT_COOKIE, "forged-session-token")
        assert client.get("/v1/documents").status_code == 401


def test_account_registration_can_remain_invite_only(tmp_path):
    settings = _account_settings(tmp_path)
    settings.account_registration_enabled = False
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        signin = client.get("/signin")
        assert signin.status_code == 200
        assert "Create account" not in signin.text
        response = _register(client, "blocked@example.com")
        assert response.status_code == 403
        assert response.json()["detail"] == "Account registration is invite-only"


def test_account_and_shared_pilot_modes_cannot_be_mixed(tmp_path):
    settings = _account_settings(tmp_path)
    settings.pilot_access_code = "correct-horse-battery-staple"
    with pytest.raises(RuntimeError, match="mutually exclusive"):
        with TestClient(create_app(settings), base_url="https://testserver"):
            pass
