from __future__ import annotations

import hashlib
import io
import json
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.accounts import ACCOUNT_COOKIE
from app.config import Settings
from app.main import create_app

PASSWORD = "account-controls-correct-horse-password"


class CapturingInvitationSender:
    def __init__(self) -> None:
        self.invitations: list[tuple[str, str, str]] = []

    def send_verification(self, recipient: str, display_name: str, url: str) -> None:
        del recipient, display_name, url

    def send_password_reset(self, recipient: str, display_name: str, url: str) -> None:
        del recipient, display_name, url

    def send_invitation(self, recipient: str, workspace_name: str, url: str) -> None:
        self.invitations.append((recipient, workspace_name, url))


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        db_path=tmp_path / "account-controls.db",
        document_store_path=tmp_path / "originals",
        accounts_enabled=True,
        account_registration_enabled=True,
        account_invitations_enabled=True,
        account_export_enabled=True,
        account_deletion_enabled=True,
        public_base_url="https://accounts.example.test",
        ingestion_worker_enabled=False,
        rate_limit_per_minute=10_000,
    )


def _register(client: TestClient, email: str, name: str) -> dict:
    response = client.post(
        "/v1/auth/register",
        json={"email": email, "display_name": name, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str) -> dict:
    response = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


def _invitation_token(sender: CapturingInvitationSender) -> str:
    return parse_qs(urlsplit(sender.invitations[-1][2]).query)["token"][0]


def test_invitation_is_one_time_scoped_and_supports_workspace_switching(tmp_path):
    sender = CapturingInvitationSender()
    app = create_app(_settings(tmp_path), account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        owner = _register(client, "owner@example.com", "Owner Account")
        owner_workspace_id = owner["workspace_id"]

        client.cookies.clear()
        guest_personal = _register(client, "guest@example.com", "Guest Account")
        guest_workspace_id = guest_personal["workspace_id"]

        client.cookies.clear()
        owner = _login(client, "owner@example.com")
        missing_csrf = client.post(
            "/v1/account/invitations",
            json={"email": "guest@example.com", "role": "reader"},
        )
        assert missing_csrf.status_code == 403
        invited = client.post(
            "/v1/account/invitations",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={
                "email": "guest@example.com",
                "role": "reader",
                "clearances": ["public", "internal"],
            },
        )
        assert invited.status_code == 201, invited.text
        assert invited.json()["status"] == "pending"
        assert sender.invitations[-1][0] == "guest@example.com"
        assert sender.invitations[-1][1] == "Owner Account's workspace"
        token = _invitation_token(sender)

        temporary = client.post(
            "/v1/account/invitations",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"email": "temporary@example.com", "role": "reader"},
        )
        assert temporary.status_code == 201
        listed = client.get("/v1/account/invitations")
        assert listed.status_code == 200
        assert {item["email"] for item in listed.json()} == {
            "guest@example.com",
            "temporary@example.com",
        }
        revoked = client.delete(
            f"/v1/account/invitations/{temporary.json()['id']}",
            headers={"X-CSRF-Token": owner["csrf_token"]},
        )
        assert revoked.status_code == 204

        rotated = client.post(
            "/v1/account/invitations",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={
                "email": "guest@example.com",
                "role": "reader",
                "clearances": ["public", "internal"],
            },
        )
        assert rotated.status_code == 201
        old_token, token = token, _invitation_token(sender)

        with app.state.repository.database.connect() as connection:
            stored = connection.execute(
                """SELECT token_hash, clearances_json FROM workspace_invitations
                   WHERE email = ? AND status = 'pending'""",
                ("guest@example.com",),
            ).fetchone()
        assert stored["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in stored["token_hash"]
        assert json.loads(stored["clearances_json"]) == ["public", "internal"]

        client.cookies.clear()
        guest = _login(client, "guest@example.com")
        wrong_csrf = client.post(
            "/v1/auth/invitations/accept",
            headers={"X-CSRF-Token": "wrong"},
            json={"token": token},
        )
        assert wrong_csrf.status_code == 403
        rotated_out = client.post(
            "/v1/auth/invitations/accept",
            headers={"X-CSRF-Token": guest["csrf_token"]},
            json={"token": old_token},
        )
        assert rotated_out.status_code == 400
        accepted = client.post(
            "/v1/auth/invitations/accept",
            headers={"X-CSRF-Token": guest["csrf_token"]},
            json={"token": token},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["workspace_id"] == owner_workspace_id
        assert accepted.json()["role"] == "reader"

        replay = client.post(
            "/v1/auth/invitations/accept",
            headers={"X-CSRF-Token": accepted.json()["csrf_token"]},
            json={"token": token},
        )
        assert replay.status_code == 400

        workspaces = client.get("/v1/account/workspaces")
        assert workspaces.status_code == 200
        assert {item["id"] for item in workspaces.json()} == {
            owner_workspace_id,
            guest_workspace_id,
        }
        switched = client.post(
            f"/v1/account/workspaces/{guest_workspace_id}/switch",
            headers={"X-CSRF-Token": accepted.json()["csrf_token"]},
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["workspace_id"] == guest_workspace_id

        client.cookies.clear()
        owner = _login(client, "owner@example.com")
        blocked = client.post(
            "/v1/account/delete",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"password": PASSWORD, "confirmation": "DELETE"},
        )
        assert blocked.status_code == 409
        assert "other members" in blocked.json()["detail"]

        members = client.get("/v1/account/members")
        assert members.status_code == 200
        assert [(item["email"], item["is_owner"]) for item in members.json()] == [
            ("owner@example.com", True),
            ("guest@example.com", False),
        ]
        owner_demotion = client.patch(
            f"/v1/account/members/{owner['id']}",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"role": "reader", "clearances": ["public"]},
        )
        assert owner_demotion.status_code == 409
        assert "Transfer ownership" in owner_demotion.json()["detail"]
        missing_csrf_update = client.patch(
            f"/v1/account/members/{guest['id']}",
            json={"role": "editor", "clearances": ["public", "internal"]},
        )
        assert missing_csrf_update.status_code == 403
        updated = client.patch(
            f"/v1/account/members/{guest['id']}",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={
                "role": "editor",
                "clearances": ["public", "internal", "confidential"],
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["role"] == "editor"
        assert updated.json()["clearances"][-1] == "confidential"
        with app.state.repository.database.connect() as connection:
            guest_workspace_sessions = connection.execute(
                """SELECT COUNT(*) FROM auth_sessions
                   WHERE workspace_id = ? AND user_id = ?""",
                (owner_workspace_id, guest["id"]),
            ).fetchone()[0]
        assert guest_workspace_sessions == 0

        wrong_password = client.post(
            "/v1/account/ownership/transfer",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={
                "new_owner_user_id": guest["id"],
                "password": "wrong-password",
                "confirmation": "TRANSFER",
            },
        )
        assert wrong_password.status_code == 400
        transferred = client.post(
            "/v1/account/ownership/transfer",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={
                "new_owner_user_id": guest["id"],
                "password": PASSWORD,
                "confirmation": "TRANSFER",
            },
        )
        assert transferred.status_code == 204, transferred.text

        protected_owner = client.post(
            f"/v1/account/members/{guest['id']}/remove",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"password": PASSWORD, "confirmation": "REMOVE"},
        )
        assert protected_owner.status_code == 409
        assert "owner cannot be removed" in protected_owner.json()["detail"]

        deleted_after_handoff = client.post(
            "/v1/account/delete",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"password": PASSWORD, "confirmation": "DELETE"},
        )
        assert deleted_after_handoff.status_code == 204, deleted_after_handoff.text
        with app.state.repository.database.connect() as connection:
            workspace = connection.execute(
                "SELECT created_by FROM workspaces WHERE id = ?", (owner_workspace_id,)
            ).fetchone()
            remaining = connection.execute(
                "SELECT role, clearances_json FROM workspace_members WHERE workspace_id = ?",
                (owner_workspace_id,),
            ).fetchall()
            deleted_user = connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (owner["id"],)
            ).fetchone()
        assert workspace["created_by"] == guest["id"]
        assert len(remaining) == 1
        assert remaining[0]["role"] == "admin"
        assert json.loads(remaining[0]["clearances_json"])[-1] == "confidential"
        assert deleted_user is None


def test_member_can_leave_and_admin_can_remove_without_deleting_account(tmp_path):
    sender = CapturingInvitationSender()
    app = create_app(_settings(tmp_path), account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        owner = _register(client, "owner@example.com", "Owner Account")
        owner_workspace_id = owner["workspace_id"]
        client.cookies.clear()
        guest = _register(client, "guest@example.com", "Guest Account")
        guest_workspace_id = guest["workspace_id"]

        client.cookies.clear()
        owner = _login(client, "owner@example.com")
        invited = client.post(
            "/v1/account/invitations",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"email": guest["email"], "role": "reader"},
        )
        assert invited.status_code == 201
        token = _invitation_token(sender)

        client.cookies.clear()
        guest = _login(client, "guest@example.com")
        accepted = client.post(
            "/v1/auth/invitations/accept",
            headers={"X-CSRF-Token": guest["csrf_token"]},
            json={"token": token},
        )
        assert accepted.status_code == 200
        left = client.post(
            "/v1/account/workspaces/current/leave",
            headers={"X-CSRF-Token": accepted.json()["csrf_token"]},
            json={"password": PASSWORD, "confirmation": "LEAVE"},
        )
        assert left.status_code == 200, left.text
        assert left.json()["workspace_id"] == guest_workspace_id

        client.cookies.clear()
        owner = _login(client, "owner@example.com")
        reinvited = client.post(
            "/v1/account/invitations",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"email": guest["email"], "role": "reader"},
        )
        assert reinvited.status_code == 201
        second_token = _invitation_token(sender)
        client.cookies.clear()
        guest = _login(client, "guest@example.com")
        assert (
            client.post(
                "/v1/auth/invitations/accept",
                headers={"X-CSRF-Token": guest["csrf_token"]},
                json={"token": second_token},
            ).status_code
            == 200
        )

        client.cookies.clear()
        owner = _login(client, "owner@example.com")
        wrong_password = client.post(
            f"/v1/account/members/{guest['id']}/remove",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"password": "wrong-password", "confirmation": "REMOVE"},
        )
        assert wrong_password.status_code == 400
        removed = client.post(
            f"/v1/account/members/{guest['id']}/remove",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"password": PASSWORD, "confirmation": "REMOVE"},
        )
        assert removed.status_code == 204, removed.text
        with app.state.repository.database.connect() as connection:
            membership = connection.execute(
                """SELECT 1 FROM workspace_members
                   WHERE workspace_id = ? AND user_id = ?""",
                (owner_workspace_id, guest["id"]),
            ).fetchone()
            account = connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (guest["id"],)
            ).fetchone()
        assert membership is None
        assert account is not None


def test_anonymous_invitation_creates_account_without_exposing_token(tmp_path):
    sender = CapturingInvitationSender()
    app = create_app(_settings(tmp_path), account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        owner = _register(client, "owner@example.com", "Owner Account")
        invited = client.post(
            "/v1/account/invitations",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"email": "new@example.com", "role": "editor"},
        )
        assert invited.status_code == 201
        token = _invitation_token(sender)
        invitation_page = client.get(f"/invitation?token={token}")
        assert invitation_page.status_code == 200
        assert token in invitation_page.text

        client.cookies.clear()
        accepted = client.post(
            "/v1/auth/invitations/accept",
            json={
                "token": token,
                "display_name": "New Teammate",
                "password": PASSWORD,
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["email"] == "new@example.com"
        assert accepted.json()["role"] == "editor"
        assert ACCOUNT_COOKIE in client.cookies
        only_workspace = client.post(
            "/v1/account/workspaces/current/leave",
            headers={"X-CSRF-Token": accepted.json()["csrf_token"]},
            json={"password": PASSWORD, "confirmation": "LEAVE"},
        )
        assert only_workspace.status_code == 409
        assert "only workspace" in only_workspace.json()["detail"]


def test_workspace_export_excludes_secrets_and_deletion_cleans_storage(tmp_path):
    sender = CapturingInvitationSender()
    settings = _settings(tmp_path)
    app = create_app(settings, account_email_sender=sender)
    with TestClient(app, base_url="https://testserver") as client:
        owner = _register(client, "export@example.com", "Export Owner")
        uploaded = client.post(
            "/v1/documents",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            files={
                "file": (
                    "policy.txt",
                    b"Synthetic policy evidence for a private workspace.",
                    "text/plain",
                )
            },
            data={"classification": "internal"},
        )
        assert uploaded.status_code == 201, uploaded.text
        with app.state.repository.database.connect() as connection:
            reference = connection.execute(
                "SELECT storage_path FROM documents WHERE id = ?",
                (uploaded.json()["id"],),
            ).fetchone()["storage_path"]
        original_path = settings.document_store_path / reference.removeprefix("local:")
        assert original_path.is_file()

        review = client.post(
            "/v1/pilot-reviews",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={
                "reviewer_context": "general",
                "ease_score": 5,
                "trust_score": 5,
                "usefulness_score": 4,
                "completed_tasks": 5,
                "would_return": "yes",
            },
        )
        assert review.status_code == 201, review.text

        exported = client.post("/v1/account/export", headers={"X-CSRF-Token": owner["csrf_token"]})
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-type"] == "application/zip"
        with ZipFile(io.BytesIO(exported.content)) as archive:
            names = archive.namelist()
            assert "manifest.json" in names
            assert any(name.startswith("originals/") for name in names)
            manifest_text = archive.read("manifest.json").decode()
            manifest = json.loads(manifest_text)
        assert manifest["scope"] == "complete_current_workspace"
        assert manifest["documents"][0]["has_original"] is True
        assert len(manifest["pilot_reviews"]) == 1
        assert "password_hash" not in manifest_text
        assert "token_hash" not in manifest_text
        assert "storage_path" not in manifest_text

        wrong_password = client.post(
            "/v1/account/delete",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"password": "wrong-password", "confirmation": "DELETE"},
        )
        assert wrong_password.status_code == 400
        deleted = client.post(
            "/v1/account/delete",
            headers={"X-CSRF-Token": owner["csrf_token"]},
            json={"password": PASSWORD, "confirmation": "DELETE"},
        )
        assert deleted.status_code == 204, deleted.text
        assert ACCOUNT_COOKIE not in client.cookies
        assert not original_path.exists()
        with app.state.repository.database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM pilot_reviews").fetchone()[0] == 0


def test_account_panel_exposes_member_ownership_and_leave_controls(tmp_path):
    app = create_app(_settings(tmp_path), account_email_sender=CapturingInvitationSender())
    with TestClient(app, base_url="https://testserver") as client:
        _register(client, "panel@example.com", "Panel Owner")
        page = client.get("/")
        assert page.status_code == 200
        assert 'id="memberSection"' in page.text
        assert 'id="memberActionPassword"' in page.text
        assert 'id="leaveWorkspace"' in page.text
        assert "Make owner" in page.text
        assert "/v1/account/ownership/transfer" in page.text


def test_account_controls_fail_closed_when_not_enabled(tmp_path):
    settings = _settings(tmp_path).model_copy(
        update={
            "account_invitations_enabled": False,
            "account_export_enabled": False,
            "account_deletion_enabled": False,
        }
    )
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as client:
        owner = _register(client, "closed@example.com", "Closed Controls")
        headers = {"X-CSRF-Token": owner["csrf_token"]}
        assert client.get("/v1/account/invitations").status_code == 404
        assert client.get("/v1/account/members").status_code == 404
        assert (
            client.post(
                "/v1/account/ownership/transfer",
                headers=headers,
                json={
                    "new_owner_user_id": owner["id"],
                    "password": PASSWORD,
                    "confirmation": "TRANSFER",
                },
            ).status_code
            == 404
        )
        assert client.post("/v1/account/export", headers=headers).status_code == 404
        assert (
            client.post(
                "/v1/account/delete",
                headers=headers,
                json={"password": PASSWORD, "confirmation": "DELETE"},
            ).status_code
            == 404
        )
