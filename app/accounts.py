from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.db import DatabaseLike
from app.models import AccountOut, Principal

ACCOUNT_COOKIE = "evidencedesk_session"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class AccountExistsError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_encode(salt)}${_encode(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_decode(expected)),
        )
        return hmac.compare_digest(derived, _decode(expected))
    except (ValueError, TypeError):
        return False


_DUMMY_PASSWORD_HASH = hash_password("evidencedesk-dummy-password")


@dataclass(frozen=True)
class AccountSession:
    token: str
    csrf_token: str
    user_id: str
    email: str
    display_name: str
    workspace_id: str
    workspace_name: str
    role: str
    clearances: list[str]
    plan: str
    expires_at: datetime

    @property
    def principal(self) -> Principal:
        return Principal(
            subject=self.user_id,
            tenant_id=self.workspace_id,
            role=self.role,
            clearances=self.clearances,
        )

    def public(self) -> AccountOut:
        return AccountOut(
            id=self.user_id,
            email=self.email,
            display_name=self.display_name,
            workspace_id=self.workspace_id,
            workspace_name=self.workspace_name,
            role=self.role,
            clearances=self.clearances,
            plan=self.plan,
            csrf_token=self.csrf_token,
        )


@dataclass(frozen=True)
class AccountEmailAction:
    token: str
    email: str
    display_name: str


@dataclass(frozen=True)
class WorkspaceInvitationAction:
    invitation_id: str
    token: str
    email: str
    workspace_name: str
    role: str
    clearances: list[str]
    expires_at: datetime


class InvitationError(ValueError):
    pass


class AccountDeletionBlocked(ValueError):
    pass


class MembershipError(ValueError):
    pass


class AccountStore:
    """Small account boundary that can later be replaced by a managed identity service."""

    def __init__(self, database: DatabaseLike, session_hours: int):
        self.database = database
        self.session_hours = session_hours

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def register(self, email: str, display_name: str, password: str) -> AccountSession:
        now = _now()
        user_id = str(uuid4())
        workspace_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        expires_at = now + timedelta(hours=self.session_hours)
        workspace_name = f"{display_name}'s workspace"
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO users
                       (id, email, display_name, password_hash, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                    (
                        user_id,
                        email,
                        display_name,
                        hash_password(password),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    """INSERT INTO workspaces (id, name, plan, created_by, created_at)
                       VALUES (?, ?, 'free', ?, ?)""",
                    (workspace_id, workspace_name, user_id, now.isoformat()),
                )
                connection.execute(
                    """INSERT INTO workspace_members
                       (workspace_id, user_id, role, clearances_json, created_at)
                       VALUES (?, ?, 'admin', ?, ?)""",
                    (
                        workspace_id,
                        user_id,
                        json.dumps(["public", "internal", "confidential"]),
                        now.isoformat(),
                    ),
                )
                self._insert_session(
                    connection, token, csrf_token, user_id, workspace_id, now, expires_at
                )
        except Exception as exc:
            constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
            if "users.email" in str(exc).lower() or constraint in {
                "users_email_key",
                "idx_users_email_ci",
            }:
                raise AccountExistsError("An account with this email already exists") from exc
            raise
        return AccountSession(
            token=token,
            csrf_token=csrf_token,
            user_id=user_id,
            email=email,
            display_name=display_name,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            role="admin",
            clearances=["public", "internal", "confidential"],
            plan="free",
            expires_at=expires_at,
        )

    def register_pending(
        self,
        email: str,
        display_name: str,
        password: str,
        token_hours: int,
    ) -> AccountEmailAction:
        now = _now()
        user_id = str(uuid4())
        workspace_id = str(uuid4())
        workspace_name = f"{display_name}'s workspace"
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO users
                       (id, email, display_name, password_hash, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'pending_verification', ?, ?)""",
                    (
                        user_id,
                        email,
                        display_name,
                        hash_password(password),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    """INSERT INTO workspaces (id, name, plan, created_by, created_at)
                       VALUES (?, ?, 'free', ?, ?)""",
                    (workspace_id, workspace_name, user_id, now.isoformat()),
                )
                connection.execute(
                    """INSERT INTO workspace_members
                       (workspace_id, user_id, role, clearances_json, created_at)
                       VALUES (?, ?, 'admin', ?, ?)""",
                    (
                        workspace_id,
                        user_id,
                        json.dumps(["public", "internal", "confidential"]),
                        now.isoformat(),
                    ),
                )
                token = self._issue_token(
                    connection,
                    user_id,
                    "verify_email",
                    now,
                    now + timedelta(hours=token_hours),
                )
        except Exception as exc:
            constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
            if "users.email" in str(exc).lower() or constraint in {
                "users_email_key",
                "idx_users_email_ci",
            }:
                raise AccountExistsError("An account with this email already exists") from exc
            raise
        return AccountEmailAction(token=token, email=email, display_name=display_name)

    @staticmethod
    def _issue_token(
        connection: Any,
        user_id: str,
        purpose: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> str:
        token = secrets.token_urlsafe(32)
        connection.execute(
            """UPDATE account_tokens SET used_at = ?
               WHERE user_id = ? AND purpose = ? AND used_at IS NULL""",
            (created_at.isoformat(), user_id, purpose),
        )
        connection.execute(
            """INSERT INTO account_tokens
               (token_hash, user_id, purpose, created_at, expires_at, used_at)
               VALUES (?, ?, ?, ?, ?, NULL)""",
            (
                AccountStore._token_hash(token),
                user_id,
                purpose,
                created_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        return token

    def issue_verification(self, email: str, token_hours: int) -> AccountEmailAction | None:
        now = _now()
        email_predicate = (
            "LOWER(email) = LOWER(?)"
            if self.database.dialect == "postgres"
            else "email = ? COLLATE NOCASE"
        )
        with self.database.connect() as connection:
            row = connection.execute(
                f"""SELECT id, email, display_name FROM users
                    WHERE {email_predicate} AND status = 'pending_verification'""",  # noqa: S608
                (email,),
            ).fetchone()
            if not row:
                return None
            token = self._issue_token(
                connection,
                row["id"],
                "verify_email",
                now,
                now + timedelta(hours=token_hours),
            )
        return AccountEmailAction(token, row["email"], row["display_name"])

    def verify_email(self, token: str) -> AccountSession | None:
        if len(token) > 200:
            return None
        now = _now()
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        session_expires = now + timedelta(hours=self.session_hours)
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT u.id user_id, u.email, u.display_name, u.status,
                          w.id workspace_id, w.name workspace_name, w.plan,
                          m.role, m.clearances_json
                   FROM account_tokens t
                   JOIN users u ON u.id = t.user_id
                   JOIN workspace_members m ON m.user_id = u.id
                   JOIN workspaces w ON w.id = m.workspace_id
                   WHERE t.token_hash = ? AND t.purpose = 'verify_email'
                     AND t.used_at IS NULL AND t.expires_at > ?
                   ORDER BY m.created_at LIMIT 1""",
                (self._token_hash(token), now.isoformat()),
            ).fetchone()
            if not row or row["status"] != "pending_verification":
                return None
            claimed = connection.execute(
                """UPDATE account_tokens SET used_at = ?
                   WHERE token_hash = ? AND used_at IS NULL""",
                (now.isoformat(), self._token_hash(token)),
            )
            if claimed.rowcount != 1:
                return None
            connection.execute(
                """UPDATE users
                   SET status = 'active', email_verified_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'pending_verification'""",
                (now.isoformat(), now.isoformat(), row["user_id"]),
            )
            self._insert_session(
                connection,
                session_token,
                csrf_token,
                row["user_id"],
                row["workspace_id"],
                now,
                session_expires,
            )
        return self._from_row(row, session_token, csrf_token, session_expires)

    def issue_password_reset(self, email: str, token_minutes: int) -> AccountEmailAction | None:
        now = _now()
        email_predicate = (
            "LOWER(email) = LOWER(?)"
            if self.database.dialect == "postgres"
            else "email = ? COLLATE NOCASE"
        )
        with self.database.connect() as connection:
            row = connection.execute(
                f"""SELECT id, email, display_name FROM users
                    WHERE {email_predicate} AND status = 'active'""",  # noqa: S608
                (email,),
            ).fetchone()
            if not row:
                return None
            token = self._issue_token(
                connection,
                row["id"],
                "reset_password",
                now,
                now + timedelta(minutes=token_minutes),
            )
        return AccountEmailAction(token, row["email"], row["display_name"])

    def reset_password(self, token: str, password: str) -> bool:
        if len(token) > 200:
            return False
        now = _now()
        token_hash = self._token_hash(token)
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT t.user_id FROM account_tokens t
                   JOIN users u ON u.id = t.user_id
                   WHERE t.token_hash = ? AND t.purpose = 'reset_password'
                     AND t.used_at IS NULL AND t.expires_at > ? AND u.status = 'active'""",
                (token_hash, now.isoformat()),
            ).fetchone()
            if not row:
                return False
            claimed = connection.execute(
                """UPDATE account_tokens SET used_at = ?
                   WHERE token_hash = ? AND used_at IS NULL""",
                (now.isoformat(), token_hash),
            )
            if claimed.rowcount != 1:
                return False
            connection.execute(
                """UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?""",
                (hash_password(password), now.isoformat(), row["user_id"]),
            )
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (row["user_id"],))
            connection.execute(
                """UPDATE account_tokens SET used_at = ?
                   WHERE user_id = ? AND purpose = 'reset_password' AND used_at IS NULL""",
                (now.isoformat(), row["user_id"]),
            )
        return True

    def create_invitation(
        self,
        session: AccountSession,
        email: str,
        role: str,
        clearances: list[str],
        invitation_hours: int,
    ) -> WorkspaceInvitationAction:
        now = _now()
        expires_at = now + timedelta(hours=invitation_hours)
        invitation_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        email_predicate = (
            "LOWER(u.email) = LOWER(?)"
            if self.database.dialect == "postgres"
            else "u.email = ? COLLATE NOCASE"
        )
        with self.database.connect() as connection:
            existing = connection.execute(
                f"""SELECT 1 FROM workspace_members m JOIN users u ON u.id = m.user_id
                    WHERE m.workspace_id = ? AND {email_predicate}""",  # noqa: S608
                (session.workspace_id, email),
            ).fetchone()
            if existing:
                raise InvitationError("This email is already a workspace member")
            connection.execute(
                """UPDATE workspace_invitations SET status = 'revoked'
                   WHERE workspace_id = ? AND LOWER(email) = LOWER(?) AND status = 'pending'""",
                (session.workspace_id, email),
            )
            connection.execute(
                """INSERT INTO workspace_invitations
                   (id, workspace_id, email, role, clearances_json, token_hash,
                    invited_by, status, created_at, expires_at, accepted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, NULL)""",
                (
                    invitation_id,
                    session.workspace_id,
                    email,
                    role,
                    json.dumps(clearances, separators=(",", ":")),
                    self._token_hash(token),
                    session.user_id,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return WorkspaceInvitationAction(
            invitation_id,
            token,
            email,
            session.workspace_name,
            role,
            clearances,
            expires_at,
        )

    def list_invitations(self, session: AccountSession) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id, email, role, clearances_json, status, expires_at
                   FROM workspace_invitations WHERE workspace_id = ?
                   ORDER BY created_at DESC""",
                (session.workspace_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "email": row["email"],
                "role": row["role"],
                "clearances": json.loads(row["clearances_json"]),
                "status": (
                    "expired"
                    if row["status"] == "pending"
                    and datetime.fromisoformat(row["expires_at"]) <= _now()
                    else row["status"]
                ),
                "expires_at": row["expires_at"],
            }
            for row in rows
        ]

    def revoke_invitation(self, session: AccountSession, invitation_id: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """UPDATE workspace_invitations SET status = 'revoked'
                   WHERE id = ? AND workspace_id = ? AND status = 'pending'""",
                (invitation_id, session.workspace_id),
            )
            return cursor.rowcount == 1

    def accept_invitation(
        self,
        token: str,
        session: AccountSession | None,
        display_name: str | None,
        password: str | None,
    ) -> AccountSession:
        if len(token) > 200:
            raise InvitationError("Invitation is invalid or expired")
        now = _now()
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        session_expires = now + timedelta(hours=self.session_hours)
        with self.database.connect() as connection:
            invitation = connection.execute(
                """SELECT i.*, w.name workspace_name, w.plan
                   FROM workspace_invitations i
                   JOIN workspaces w ON w.id = i.workspace_id
                   WHERE i.token_hash = ? AND i.status = 'pending' AND i.expires_at > ?""",
                (self._token_hash(token), now.isoformat()),
            ).fetchone()
            if not invitation:
                raise InvitationError("Invitation is invalid or expired")
            claimed = connection.execute(
                """UPDATE workspace_invitations SET status = 'accepted', accepted_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (now.isoformat(), invitation["id"]),
            )
            if claimed.rowcount != 1:
                raise InvitationError("Invitation is invalid or expired")
            if session:
                if session.email.lower() != invitation["email"].lower():
                    raise InvitationError("Sign in with the invited email address")
                user_id = session.user_id
                email = session.email
                name = session.display_name
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?",
                    (self._token_hash(session.token),),
                )
            else:
                if not display_name or not password:
                    raise InvitationError("Sign in, or provide a name and password to join")
                email_predicate = (
                    "LOWER(email) = LOWER(?)"
                    if self.database.dialect == "postgres"
                    else "email = ? COLLATE NOCASE"
                )
                existing = connection.execute(
                    f"SELECT id FROM users WHERE {email_predicate}",  # noqa: S608
                    (invitation["email"],),
                ).fetchone()
                if existing:
                    raise InvitationError("An account exists for this email. Sign in to accept")
                user_id = str(uuid4())
                email = invitation["email"]
                name = display_name
                connection.execute(
                    """INSERT INTO users
                       (id, email, display_name, password_hash, status, email_verified_at,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
                    (
                        user_id,
                        email,
                        name,
                        hash_password(password),
                        now.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            membership = connection.execute(
                """SELECT 1 FROM workspace_members WHERE workspace_id = ? AND user_id = ?""",
                (invitation["workspace_id"], user_id),
            ).fetchone()
            if not membership:
                connection.execute(
                    """INSERT INTO workspace_members
                       (workspace_id, user_id, role, clearances_json, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        invitation["workspace_id"],
                        user_id,
                        invitation["role"],
                        invitation["clearances_json"],
                        now.isoformat(),
                    ),
                )
            self._insert_session(
                connection,
                session_token,
                csrf_token,
                user_id,
                invitation["workspace_id"],
                now,
                session_expires,
            )
        return AccountSession(
            token=session_token,
            csrf_token=csrf_token,
            user_id=user_id,
            email=email,
            display_name=name,
            workspace_id=invitation["workspace_id"],
            workspace_name=invitation["workspace_name"],
            role=invitation["role"],
            clearances=json.loads(invitation["clearances_json"]),
            plan=invitation["plan"],
            expires_at=session_expires,
        )

    def list_workspaces(self, session: AccountSession) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT w.id, w.name, w.plan, w.created_by, m.role, m.clearances_json,
                          (SELECT COUNT(*) FROM workspace_members members
                           WHERE members.workspace_id = w.id) member_count
                   FROM workspace_members m JOIN workspaces w ON w.id = m.workspace_id
                   WHERE m.user_id = ? ORDER BY w.created_at""",
                (session.user_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "plan": row["plan"],
                "created_by": row["created_by"],
                "role": row["role"],
                "clearances": json.loads(row["clearances_json"]),
                "member_count": row["member_count"],
            }
            for row in rows
        ]

    def list_members(self, session: AccountSession) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT u.id, u.email, u.display_name, m.role, m.clearances_json,
                          m.created_at joined_at, w.created_by
                   FROM workspace_members m
                   JOIN users u ON u.id = m.user_id
                   JOIN workspaces w ON w.id = m.workspace_id
                   WHERE m.workspace_id = ? AND u.status = 'active'
                   ORDER BY CASE WHEN u.id = w.created_by THEN 0 ELSE 1 END,
                            m.created_at, LOWER(u.email)""",
                (session.workspace_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "email": row["email"],
                "display_name": row["display_name"],
                "role": row["role"],
                "clearances": json.loads(row["clearances_json"]),
                "joined_at": row["joined_at"],
                "is_owner": row["id"] == row["created_by"],
            }
            for row in rows
        ]

    @staticmethod
    def _verify_current_password(connection: Any, user_id: str, password: str) -> None:
        user = connection.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        encoded = user["password_hash"] if user else _DUMMY_PASSWORD_HASH
        if not user or not verify_password(password, encoded):
            raise InvitationError("Password is incorrect")

    def update_member(
        self,
        session: AccountSession,
        user_id: str,
        role: str,
        clearances: list[str],
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            workspace = connection.execute(
                "SELECT created_by FROM workspaces WHERE id = ?", (session.workspace_id,)
            ).fetchone()
            member = connection.execute(
                """SELECT u.id, u.email, u.display_name, m.created_at joined_at
                   FROM workspace_members m JOIN users u ON u.id = m.user_id
                   WHERE m.workspace_id = ? AND m.user_id = ?""",
                (session.workspace_id, user_id),
            ).fetchone()
            if not workspace or not member:
                raise MembershipError("Workspace member not found")
            if user_id == workspace["created_by"]:
                raise MembershipError("Transfer ownership before changing the owner's access")
            if user_id == session.user_id:
                raise MembershipError("You cannot change your own workspace access")
            connection.execute(
                """UPDATE workspace_members SET role = ?, clearances_json = ?
                   WHERE workspace_id = ? AND user_id = ?""",
                (
                    role,
                    json.dumps(clearances, separators=(",", ":")),
                    session.workspace_id,
                    user_id,
                ),
            )
            connection.execute(
                "DELETE FROM auth_sessions WHERE workspace_id = ? AND user_id = ?",
                (session.workspace_id, user_id),
            )
        return {
            "id": member["id"],
            "email": member["email"],
            "display_name": member["display_name"],
            "role": role,
            "clearances": clearances,
            "joined_at": member["joined_at"],
            "is_owner": False,
        }

    def remove_member(
        self, session: AccountSession, user_id: str, password: str
    ) -> None:
        with self.database.connect() as connection:
            self._verify_current_password(connection, session.user_id, password)
            workspace = connection.execute(
                "SELECT created_by FROM workspaces WHERE id = ?", (session.workspace_id,)
            ).fetchone()
            member = connection.execute(
                """SELECT 1 FROM workspace_members
                   WHERE workspace_id = ? AND user_id = ?""",
                (session.workspace_id, user_id),
            ).fetchone()
            if not workspace or not member:
                raise MembershipError("Workspace member not found")
            if user_id == session.user_id:
                raise MembershipError("Use Leave workspace to remove your own membership")
            if user_id == workspace["created_by"]:
                raise MembershipError("The workspace owner cannot be removed")
            connection.execute(
                "DELETE FROM auth_sessions WHERE workspace_id = ? AND user_id = ?",
                (session.workspace_id, user_id),
            )
            connection.execute(
                "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
                (session.workspace_id, user_id),
            )

    def transfer_ownership(
        self, session: AccountSession, new_owner_user_id: str, password: str
    ) -> None:
        with self.database.connect() as connection:
            self._verify_current_password(connection, session.user_id, password)
            workspace = connection.execute(
                "SELECT created_by FROM workspaces WHERE id = ?", (session.workspace_id,)
            ).fetchone()
            if not workspace or workspace["created_by"] != session.user_id:
                raise MembershipError("Only the current owner can transfer this workspace")
            if new_owner_user_id == session.user_id:
                raise MembershipError("Choose another workspace member as the new owner")
            target = connection.execute(
                """SELECT 1 FROM workspace_members
                   WHERE workspace_id = ? AND user_id = ?""",
                (session.workspace_id, new_owner_user_id),
            ).fetchone()
            if not target:
                raise MembershipError("The new owner must already be a workspace member")
            transferred = connection.execute(
                "UPDATE workspaces SET created_by = ? WHERE id = ? AND created_by = ?",
                (new_owner_user_id, session.workspace_id, session.user_id),
            )
            if transferred.rowcount != 1:
                raise MembershipError("Workspace ownership changed; refresh and try again")
            connection.execute(
                """UPDATE workspace_members
                   SET role = 'admin', clearances_json = ?
                   WHERE workspace_id = ? AND user_id = ?""",
                (
                    '["public","internal","confidential"]',
                    session.workspace_id,
                    new_owner_user_id,
                ),
            )
            connection.execute(
                "DELETE FROM auth_sessions WHERE workspace_id = ? AND user_id = ?",
                (session.workspace_id, new_owner_user_id),
            )

    def leave_workspace(self, session: AccountSession, password: str) -> AccountSession:
        now = _now()
        with self.database.connect() as connection:
            self._verify_current_password(connection, session.user_id, password)
            workspace = connection.execute(
                "SELECT created_by FROM workspaces WHERE id = ?", (session.workspace_id,)
            ).fetchone()
            if not workspace:
                raise MembershipError("Workspace not found")
            if workspace["created_by"] == session.user_id:
                raise MembershipError("Transfer ownership before leaving this workspace")
            replacement = connection.execute(
                """SELECT u.id user_id, u.email, u.display_name,
                          w.id workspace_id, w.name workspace_name, w.plan,
                          m.role, m.clearances_json
                   FROM users u JOIN workspace_members m ON m.user_id = u.id
                   JOIN workspaces w ON w.id = m.workspace_id
                   WHERE u.id = ? AND w.id <> ? AND u.status = 'active'
                   ORDER BY m.created_at LIMIT 1""",
                (session.user_id, session.workspace_id),
            ).fetchone()
            if not replacement:
                raise MembershipError(
                    "This is your only workspace; delete the account instead of leaving it"
                )
            connection.execute(
                "DELETE FROM auth_sessions WHERE workspace_id = ? AND user_id = ?",
                (session.workspace_id, session.user_id),
            )
            connection.execute(
                "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
                (session.workspace_id, session.user_id),
            )
            token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(24)
            expires_at = now + timedelta(hours=self.session_hours)
            self._insert_session(
                connection,
                token,
                csrf_token,
                session.user_id,
                replacement["workspace_id"],
                now,
                expires_at,
            )
        return self._from_row(replacement, token, csrf_token, expires_at)

    def switch_workspace(self, session: AccountSession, workspace_id: str) -> AccountSession | None:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT u.id user_id, u.email, u.display_name,
                          w.id workspace_id, w.name workspace_name, w.plan,
                          m.role, m.clearances_json
                   FROM users u JOIN workspace_members m ON m.user_id = u.id
                   JOIN workspaces w ON w.id = m.workspace_id
                   WHERE u.id = ? AND w.id = ? AND u.status = 'active'""",
                (session.user_id, workspace_id),
            ).fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(24)
            expires_at = now + timedelta(hours=self.session_hours)
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (self._token_hash(session.token),),
            )
            self._insert_session(
                connection, token, csrf_token, session.user_id, workspace_id, now, expires_at
            )
        return self._from_row(row, token, csrf_token, expires_at)

    def delete_account(self, session: AccountSession, password: str) -> tuple[list[str], list[str]]:
        with self.database.connect() as connection:
            self._verify_current_password(connection, session.user_id, password)
            owned = connection.execute(
                """SELECT w.id, w.name,
                          (SELECT COUNT(*) FROM workspace_members m
                           WHERE m.workspace_id = w.id) member_count
                   FROM workspaces w WHERE w.created_by = ?""",
                (session.user_id,),
            ).fetchall()
            shared = [row["name"] for row in owned if row["member_count"] > 1]
            if shared:
                raise AccountDeletionBlocked(
                    "Transfer or remove other members before deleting this account"
                )
            workspace_ids = [row["id"] for row in owned]
            original_paths: list[str] = []
            staged_paths: list[str] = []
            for workspace_id in workspace_ids:
                original_paths.extend(
                    row["storage_path"]
                    for row in connection.execute(
                        """SELECT storage_path FROM documents
                           WHERE tenant_id = ? AND storage_path IS NOT NULL""",
                        (workspace_id,),
                    ).fetchall()
                )
                staged_paths.extend(
                    row["storage_path"]
                    for row in connection.execute(
                        """SELECT storage_path FROM ingestion_jobs
                           WHERE tenant_id = ? AND storage_path IS NOT NULL""",
                        (workspace_id,),
                    ).fetchall()
                )
                connection.execute("DELETE FROM feedback WHERE tenant_id = ?", (workspace_id,))
                connection.execute(
                    "DELETE FROM pilot_reviews WHERE tenant_id = ?", (workspace_id,)
                )
                connection.execute(
                    "DELETE FROM answer_diagnostics WHERE tenant_id = ?", (workspace_id,)
                )
                connection.execute(
                    "DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE tenant_id = ?)",
                    (workspace_id,),
                )
                connection.execute("DELETE FROM conversations WHERE tenant_id = ?", (workspace_id,))
                for table in (
                    "ingestion_jobs",
                    "audit_events",
                    "payment_events",
                    "model_usage_events",
                    "model_usage_daily",
                    "documents",
                ):
                    connection.execute(
                        f'DELETE FROM "{table}" WHERE tenant_id = ?', (workspace_id,)
                    )
                connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            connection.execute(
                "UPDATE workspace_invitations SET status = 'revoked' WHERE invited_by = ? AND status = 'pending'",
                (session.user_id,),
            )
            connection.execute(
                "DELETE FROM workspace_invitations WHERE invited_by = ?", (session.user_id,)
            )
            connection.execute(
                "DELETE FROM workspace_members WHERE user_id = ?", (session.user_id,)
            )
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (session.user_id,))
            connection.execute("DELETE FROM account_tokens WHERE user_id = ?", (session.user_id,))
            connection.execute("DELETE FROM users WHERE id = ?", (session.user_id,))
        return original_paths, staged_paths

    def login(self, email: str, password: str) -> AccountSession | None:
        email_predicate = (
            "LOWER(u.email) = LOWER(?)"
            if self.database.dialect == "postgres"
            else "u.email = ? COLLATE NOCASE"
        )
        with self.database.connect() as connection:
            row = connection.execute(
                f"""SELECT u.id user_id, u.email, u.display_name, u.password_hash, u.status,
                          w.id workspace_id, w.name workspace_name, w.plan,
                          m.role, m.clearances_json
                   FROM users u
                   LEFT JOIN workspace_members m ON m.user_id = u.id
                   LEFT JOIN workspaces w ON w.id = m.workspace_id
                   WHERE {email_predicate}
                   ORDER BY m.created_at LIMIT 1""",  # noqa: S608
                (email,),
            ).fetchone()
        encoded = row["password_hash"] if row else _DUMMY_PASSWORD_HASH
        password_matches = verify_password(password, encoded)
        if not row or not password_matches or row["status"] != "active" or not row["workspace_id"]:
            return None
        now = _now()
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        expires_at = now + timedelta(hours=self.session_hours)
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?", (now.isoformat(),)
            )
            self._insert_session(
                connection,
                token,
                csrf_token,
                row["user_id"],
                row["workspace_id"],
                now,
                expires_at,
            )
        return self._from_row(row, token, csrf_token, expires_at)

    @staticmethod
    def _insert_session(
        connection: Any,
        token: str,
        csrf_token: str,
        user_id: str,
        workspace_id: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        connection.execute(
            """INSERT INTO auth_sessions
               (token_hash, user_id, workspace_id, csrf_token, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                AccountStore._token_hash(token),
                user_id,
                workspace_id,
                csrf_token,
                created_at.isoformat(),
                expires_at.isoformat(),
            ),
        )

    def resolve(self, token: str | None) -> AccountSession | None:
        if not token or len(token) > 200:
            return None
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT u.id user_id, u.email, u.display_name, u.status,
                          w.id workspace_id, w.name workspace_name, w.plan,
                          m.role, m.clearances_json, s.csrf_token, s.expires_at
                   FROM auth_sessions s
                   JOIN users u ON u.id = s.user_id
                   JOIN workspaces w ON w.id = s.workspace_id
                   JOIN workspace_members m
                     ON m.workspace_id = s.workspace_id AND m.user_id = s.user_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""",
                (self._token_hash(token), now.isoformat()),
            ).fetchone()
        if not row or row["status"] != "active":
            return None
        return self._from_row(
            row,
            token,
            row["csrf_token"],
            datetime.fromisoformat(row["expires_at"]),
        )

    def logout(self, token: str | None) -> None:
        if not token or len(token) > 200:
            return
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (self._token_hash(token),)
            )

    @staticmethod
    def _from_row(
        row: Mapping[str, Any], token: str, csrf_token: str, expires_at: datetime
    ) -> AccountSession:
        return AccountSession(
            token=token,
            csrf_token=csrf_token,
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            workspace_id=row["workspace_id"],
            workspace_name=row["workspace_name"],
            role=row["role"],
            clearances=json.loads(row["clearances_json"]),
            plan=row["plan"],
            expires_at=expires_at,
        )


def account_page(
    registration_enabled: bool,
    verification_required: bool = False,
    password_reset_enabled: bool = False,
    next_path: str = "/",
) -> str:
    registration_tab = (
        '<button class="mode" data-mode="register" type="button">Create account</button>'
        if registration_enabled
        else ""
    )
    registration_note = (
        (
            "Create a private workspace, then verify your email to activate it."
            if verification_required
            else "Create a private workspace in under a minute."
        )
        if registration_enabled
        else "New account registration is currently invite-only."
    )
    forgot_link = (
        '<a class="account-link" href="/forgot-password">Forgot your password?</a>'
        if password_reset_enabled
        else ""
    )
    registration_prompt = json.dumps(
        "We will email a one-time link before activating your private workspace."
        if verification_required
        else "Your documents and conversations stay inside your workspace."
    )
    next_path_json = json.dumps(next_path).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive">
<title>Sign in · EvidenceDesk</title><style>
:root{{color-scheme:dark;--gold:#ffc35c;--green:#3deba0;--ink:#f6f7ef;--muted:#9ca9a1;--line:#213c2e}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:#050907;color:var(--ink);font:14px/1.55 Inter,"Segoe UI",system-ui,sans-serif;display:grid;place-items:center;padding:24px;overflow-x:hidden}}body:before{{content:"";position:fixed;inset:0;background:radial-gradient(circle at 18% 12%,#744cff2c,transparent 30%),radial-gradient(circle at 82% 85%,#25d99720,transparent 34%),linear-gradient(#ffffff05 1px,transparent 1px),linear-gradient(90deg,#ffffff04 1px,transparent 1px);background-size:auto,auto,54px 54px,54px 54px;pointer-events:none}}.shell{{position:relative;width:min(980px,100%);display:grid;grid-template-columns:1.08fr .92fr;border:1px solid var(--line);border-radius:25px;overflow:hidden;background:#09110ded;box-shadow:0 45px 140px #000b}}.story{{padding:60px;background:linear-gradient(145deg,#101c16,#07100c);min-height:610px;display:flex;flex-direction:column}}.brand{{display:flex;align-items:center;gap:12px;font:650 23px Georgia,serif}}.sigil{{width:42px;height:42px;display:grid;place-items:center;border:1px solid #ffc35c99;border-radius:12px;color:var(--gold);font:800 10px ui-monospace,monospace}}.kicker{{margin-top:auto;color:var(--green);font:800 10px ui-monospace,monospace;letter-spacing:.18em;text-transform:uppercase}}h1{{font:650 clamp(40px,6vw,70px)/.95 Georgia,serif;letter-spacing:-.055em;margin:16px 0 22px;max-width:590px}}h1 span{{display:block;color:var(--gold)}}.deck{{max-width:520px;color:#b6c3bb;font-size:16px}}.proof{{display:flex;gap:9px;flex-wrap:wrap;margin-top:30px}}.proof span{{border:1px solid var(--line);border-radius:22px;padding:7px 10px;color:var(--muted);font-size:10px}}.auth{{padding:48px 42px;background:#0c1511;display:flex;flex-direction:column;justify-content:center}}.maker{{display:flex;align-items:center;gap:10px;margin-bottom:30px}}.maker img{{width:42px;height:42px;border-radius:12px;object-fit:cover;border:1px solid #ffc35c66}}.maker b{{display:block;color:var(--gold);letter-spacing:.15em;font-size:11px}}.maker small{{color:var(--muted);font-size:8px;text-transform:uppercase;letter-spacing:.11em}}.modes{{display:flex;gap:5px;padding:4px;border:1px solid var(--line);border-radius:12px;margin:18px 0;background:#07100c}}.mode{{flex:1;border:0;border-radius:8px;padding:9px;background:transparent;color:var(--muted);font-weight:750}}.mode.active{{background:#17261e;color:var(--ink)}}label{{display:block;margin:14px 0 6px;color:var(--muted);font-size:9px;font-weight:800;letter-spacing:.13em;text-transform:uppercase}}input{{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:#07100c;color:var(--ink);font:inherit}}input:focus{{outline:3px solid #3deba02c;border-color:var(--green)}}button.submit{{width:100%;margin-top:20px;padding:12px;border:0;border-radius:11px;background:linear-gradient(135deg,#ffd47e,#b67921);color:#151008;font-weight:850;cursor:pointer}}button:disabled{{opacity:.55;cursor:wait}}.status{{min-height:22px;margin-top:12px;color:var(--muted);font-size:12px}}.status.error{{color:#ff9a92}}.fine{{margin-top:20px;color:#75847b;font-size:10px}}.account-link{{display:inline-block;margin-top:8px;color:var(--gold);font-size:11px;text-decoration:none}}.account-link:hover{{text-decoration:underline}}.hidden{{display:none}}@media(max-width:760px){{.shell{{grid-template-columns:1fr}}.story{{min-height:330px;padding:34px}}.auth{{padding:34px 26px}}h1{{font-size:46px}}}}
</style></head><body><main class="shell"><section class="story"><div class="brand"><span class="sigil">ED</span>EvidenceDesk</div><div class="kicker">Private evidence workspace</div><h1>Answers should earn trust.<span>Start with the source.</span></h1><p class="deck">Upload approved organizational documents, ask in normal language, and inspect the exact evidence behind each supported answer.</p><div class="proof"><span>Private workspace</span><span>Page citations</span><span>Safe refusal</span><span>OCR ready</span></div></section><section class="auth"><div class="maker"><img src="/developer-signature.png" alt="IZAQ developer portrait"><div><b>IZAQ</b><small>Product direction · design × engineering</small></div></div><div><h2 id="title">Welcome back</h2><p id="subtitle" style="color:var(--muted)">{registration_note}</p></div><div class="modes"><button class="mode active" data-mode="login" type="button">Sign in</button>{registration_tab}</div><form id="form"><div id="nameBlock" class="hidden"><label for="name">Your name</label><input id="name" autocomplete="name" maxlength="80"></div><label for="email">Email address</label><input id="email" type="email" autocomplete="email" maxlength="254" required><label for="password">Password</label><input id="password" type="password" autocomplete="current-password" maxlength="128" required><button id="submit" class="submit">Enter EvidenceDesk →</button><div id="status" class="status" role="status" aria-live="polite"></div></form>{forgot_link}<p class="fine">Sessions use secure HttpOnly cookies. Your workspace is isolated from other users before document retrieval.</p></section></main><script>
const modes=[...document.querySelectorAll('[data-mode]')],nameBlock=document.querySelector('#nameBlock'),form=document.querySelector('#form'),status=document.querySelector('#status'),submit=document.querySelector('#submit');let mode='login';
modes.forEach(button=>button.onclick=()=>{{mode=button.dataset.mode;modes.forEach(item=>item.classList.toggle('active',item===button));let register=mode==='register';nameBlock.classList.toggle('hidden',!register);document.querySelector('#name').required=register;document.querySelector('#password').autocomplete=register?'new-password':'current-password';document.querySelector('#title').textContent=register?'Create your private workspace':'Welcome back';document.querySelector('#subtitle').textContent=register?{registration_prompt}:'Sign in to continue to your evidence workspace.';submit.textContent=register?'Create workspace →':'Enter EvidenceDesk →'}});
form.onsubmit=async event=>{{event.preventDefault();status.textContent=mode==='register'?'Creating your secure workspace…':'Verifying your account…';status.className='status';submit.disabled=true;let body={{email:document.querySelector('#email').value,password:document.querySelector('#password').value}};if(mode==='register')body.display_name=document.querySelector('#name').value;try{{let response=await fetch('/v1/auth/'+mode,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}}),data=await response.json().catch(()=>({{detail:'Unexpected response'}}));if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Please check the form and try again.');if(data.action_required==='verify_email'){{status.textContent=data.message;submit.disabled=false;return}}location.replace({next_path_json})}}catch(error){{status.textContent=error.message;status.className='status error';submit.disabled=false}}}};
</script></body></html>"""


def invitation_page(token: str) -> str:
    token_json = json.dumps(token).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>Join workspace · EvidenceDesk</title><style>:root{{color-scheme:dark;--gold:#ffc35c;--green:#3deba0;--ink:#f6f7ef;--muted:#9ca9a1;--line:#213c2e}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(circle at 15% 12%,#744cff2c,transparent 30%),radial-gradient(circle at 85% 80%,#25d99720,transparent 35%),#050907;color:var(--ink);font:14px/1.55 Inter,"Segoe UI",system-ui,sans-serif}}main{{width:min(540px,100%);padding:42px;border:1px solid var(--line);border-radius:24px;background:#0c1511;box-shadow:0 35px 110px #000b}}.brand{{color:var(--gold);font:700 13px ui-monospace,monospace;letter-spacing:.16em}}h1{{font:650 46px/1 Georgia,serif;letter-spacing:-.04em;margin:24px 0 14px}}p{{color:var(--muted)}}label{{display:block;margin:19px 0 7px;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.12em}}input{{width:100%;padding:13px;border:1px solid var(--line);border-radius:11px;background:#07100c;color:var(--ink);font:inherit}}button{{width:100%;margin-top:24px;padding:13px;border:0;border-radius:11px;background:linear-gradient(135deg,#ffd47e,#b67921);color:#151008;font-weight:850;cursor:pointer}}button:disabled{{opacity:.55}}#status{{min-height:24px;margin-top:14px;color:var(--green)}}#status.error{{color:#ff9a92}}a{{color:var(--gold)}}.hidden{{display:none}}.session{{padding:12px;border:1px solid var(--line);border-radius:11px;color:var(--green);background:#07100c}}</style></head><body><main><div class="brand">EVIDENCEDESK · SECURE INVITATION</div><h1>Join the evidence workspace.</h1><p>This one-time invitation gives you only the role and document clearances selected by the workspace administrator.</p><div id="session" class="session hidden"></div><form id="form"><div id="newAccount"><label for="name">Your name</label><input id="name" autocomplete="name" minlength="2" maxlength="80"><label for="password">Create a password</label><input id="password" type="password" autocomplete="new-password" minlength="12" maxlength="128"></div><button id="submit">Accept invitation →</button><div id="status" role="status" aria-live="polite"></div></form><p id="signin"><a href="/signin">Already have an account? Sign in first</a></p></main><script>const token={token_json},form=document.querySelector('#form'),submit=document.querySelector('#submit'),status=document.querySelector('#status'),newAccount=document.querySelector('#newAccount'),sessionBox=document.querySelector('#session'),signin=document.querySelector('#signin');let csrf='',signedIn=false;const next=location.pathname+location.search;signin.querySelector('a').href='/signin?next='+encodeURIComponent(next);async function detectSession(){{const response=await fetch('/v1/auth/me');if(!response.ok)return;const account=await response.json();signedIn=true;csrf=account.csrf_token;newAccount.classList.add('hidden');signin.classList.add('hidden');sessionBox.classList.remove('hidden');sessionBox.textContent='Signed in as '+account.email+'. Accept to switch into the invited workspace.'}}detectSession();form.onsubmit=async event=>{{event.preventDefault();submit.disabled=true;status.className='';status.textContent='Accepting invitation…';const body={{token}};if(!signedIn){{body.display_name=document.querySelector('#name').value;body.password=document.querySelector('#password').value}}try{{const headers={{'Content-Type':'application/json'}};if(csrf)headers['X-CSRF-Token']=csrf;const response=await fetch('/v1/auth/invitations/accept',{{method:'POST',headers,body:JSON.stringify(body)}}),data=await response.json().catch(()=>({{detail:'Unexpected response'}}));if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Invitation could not be accepted');status.textContent='Workspace joined. Opening EvidenceDesk…';setTimeout(()=>location.replace('/'),500)}}catch(error){{status.textContent=error.message;status.className='error';submit.disabled=false}}}};</script></body></html>"""


def account_recovery_page(mode: str, token: str | None = None) -> str:
    if mode not in {"forgot", "reset", "verify"}:
        raise ValueError("Unknown account recovery page")
    token_json = json.dumps(token or "").replace("<", "\\u003c")
    configuration = {
        "forgot": {
            "title": "Recover your account",
            "description": "Enter your email. If an active account exists, we will send a one-time reset link.",
            "label": "Email address",
            "input": '<input id="value" type="email" autocomplete="email" maxlength="254" required>',
            "button": "Send reset link",
            "endpoint": "/v1/auth/password-reset/request",
            "body": "{email:value.value}",
            "success": "data.message",
            "redirect": "",
        },
        "reset": {
            "title": "Choose a new password",
            "description": "This one-time link expires quickly and will sign out every existing session.",
            "label": "New password",
            "input": '<input id="value" type="password" autocomplete="new-password" minlength="12" maxlength="128" required>',
            "button": "Reset password",
            "endpoint": "/v1/auth/password-reset/complete",
            "body": f"{{token:{token_json},password:value.value}}",
            "success": "data.message",
            "redirect": "setTimeout(()=>location.replace('/signin'),900);",
        },
        "verify": {
            "title": "Verify your email",
            "description": "Confirm this address to activate your private EvidenceDesk workspace.",
            "label": "",
            "input": "",
            "button": "Verify and enter EvidenceDesk",
            "endpoint": "/v1/auth/verify-email",
            "body": f"{{token:{token_json}}}",
            "success": "'Email verified. Opening your workspace…'",
            "redirect": "setTimeout(()=>location.replace('/'),500);",
        },
    }[mode]
    label = f'<label for="value">{configuration["label"]}</label>' if configuration["label"] else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>{configuration["title"]} · EvidenceDesk</title><style>:root{{color-scheme:dark;--gold:#ffc35c;--green:#3deba0;--ink:#f6f7ef;--muted:#9ca9a1;--line:#213c2e}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(circle at 15% 12%,#744cff2c,transparent 30%),#050907;color:var(--ink);font:14px/1.55 Inter,"Segoe UI",system-ui,sans-serif}}main{{width:min(500px,100%);padding:42px;border:1px solid var(--line);border-radius:24px;background:#0c1511;box-shadow:0 35px 110px #000b}}.brand{{color:var(--gold);font:700 13px ui-monospace,monospace;letter-spacing:.16em}}h1{{font:650 42px/1 Georgia,serif;letter-spacing:-.04em;margin:24px 0 14px}}p{{color:var(--muted)}}label{{display:block;margin:24px 0 7px;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.12em}}input{{width:100%;padding:13px;border:1px solid var(--line);border-radius:11px;background:#07100c;color:var(--ink);font:inherit}}button{{width:100%;margin-top:24px;padding:13px;border:0;border-radius:11px;background:linear-gradient(135deg,#ffd47e,#b67921);color:#151008;font-weight:850;cursor:pointer}}button:disabled{{opacity:.55}}#status{{min-height:24px;margin-top:14px;color:var(--green)}}#status.error{{color:#ff9a92}}a{{color:var(--gold)}}</style></head><body><main><div class="brand">EVIDENCEDESK · ACCOUNT SAFETY</div><h1>{configuration["title"]}</h1><p>{configuration["description"]}</p><form id="form">{label}{configuration["input"]}<button id="submit">{configuration["button"]}</button><div id="status" role="status" aria-live="polite"></div></form><p><a href="/signin">Return to sign in</a></p></main><script>const form=document.querySelector('#form'),submit=document.querySelector('#submit'),status=document.querySelector('#status'),value=document.querySelector('#value');form.onsubmit=async event=>{{event.preventDefault();submit.disabled=true;status.className='';status.textContent='Working…';try{{const response=await fetch('{configuration["endpoint"]}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({configuration["body"]})}}),data=await response.json().catch(()=>({{detail:'Unexpected response'}}));if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Request failed');status.textContent={configuration["success"]};{configuration["redirect"]}}}catch(error){{status.textContent=error.message;status.className='error';submit.disabled=false}}}};</script></body></html>"""
