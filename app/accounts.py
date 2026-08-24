from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db import Database
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


class AccountStore:
    """Small account boundary that can later be replaced by a managed identity service."""

    def __init__(self, database: Database, session_hours: int):
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
                    (user_id, email, display_name, hash_password(password), now.isoformat(), now.isoformat()),
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
        except sqlite3.IntegrityError as exc:
            if "users.email" in str(exc).lower():
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

    def login(self, email: str, password: str) -> AccountSession | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT u.id user_id, u.email, u.display_name, u.password_hash, u.status,
                          w.id workspace_id, w.name workspace_name, w.plan,
                          m.role, m.clearances_json
                   FROM users u
                   LEFT JOIN workspace_members m ON m.user_id = u.id
                   LEFT JOIN workspaces w ON w.id = m.workspace_id
                   WHERE u.email = ? COLLATE NOCASE
                   ORDER BY m.created_at LIMIT 1""",
                (email,),
            ).fetchone()
        encoded = row["password_hash"] if row else _DUMMY_PASSWORD_HASH
        password_matches = verify_password(password, encoded)
        if (
            not row
            or not password_matches
            or row["status"] != "active"
            or not row["workspace_id"]
        ):
            return None
        now = _now()
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        expires_at = now + timedelta(hours=self.session_hours)
        with self.database.connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now.isoformat(),))
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
        connection: sqlite3.Connection,
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
        row: sqlite3.Row, token: str, csrf_token: str, expires_at: datetime
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


def account_page(registration_enabled: bool) -> str:
    registration_tab = (
        '<button class="mode" data-mode="register" type="button">Create account</button>'
        if registration_enabled
        else ""
    )
    registration_note = (
        "Create a private workspace in under a minute."
        if registration_enabled
        else "New account registration is currently invite-only."
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive">
<title>Sign in · EvidenceDesk</title><style>
:root{{color-scheme:dark;--gold:#ffc35c;--green:#3deba0;--ink:#f6f7ef;--muted:#9ca9a1;--line:#213c2e}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:#050907;color:var(--ink);font:14px/1.55 Inter,"Segoe UI",system-ui,sans-serif;display:grid;place-items:center;padding:24px;overflow-x:hidden}}body:before{{content:"";position:fixed;inset:0;background:radial-gradient(circle at 18% 12%,#744cff2c,transparent 30%),radial-gradient(circle at 82% 85%,#25d99720,transparent 34%),linear-gradient(#ffffff05 1px,transparent 1px),linear-gradient(90deg,#ffffff04 1px,transparent 1px);background-size:auto,auto,54px 54px,54px 54px;pointer-events:none}}.shell{{position:relative;width:min(980px,100%);display:grid;grid-template-columns:1.08fr .92fr;border:1px solid var(--line);border-radius:25px;overflow:hidden;background:#09110ded;box-shadow:0 45px 140px #000b}}.story{{padding:60px;background:linear-gradient(145deg,#101c16,#07100c);min-height:610px;display:flex;flex-direction:column}}.brand{{display:flex;align-items:center;gap:12px;font:650 23px Georgia,serif}}.sigil{{width:42px;height:42px;display:grid;place-items:center;border:1px solid #ffc35c99;border-radius:12px;color:var(--gold);font:800 10px ui-monospace,monospace}}.kicker{{margin-top:auto;color:var(--green);font:800 10px ui-monospace,monospace;letter-spacing:.18em;text-transform:uppercase}}h1{{font:650 clamp(40px,6vw,70px)/.95 Georgia,serif;letter-spacing:-.055em;margin:16px 0 22px;max-width:590px}}h1 span{{display:block;color:var(--gold)}}.deck{{max-width:520px;color:#b6c3bb;font-size:16px}}.proof{{display:flex;gap:9px;flex-wrap:wrap;margin-top:30px}}.proof span{{border:1px solid var(--line);border-radius:22px;padding:7px 10px;color:var(--muted);font-size:10px}}.auth{{padding:48px 42px;background:#0c1511;display:flex;flex-direction:column;justify-content:center}}.maker{{display:flex;align-items:center;gap:10px;margin-bottom:30px}}.maker img{{width:42px;height:42px;border-radius:12px;object-fit:cover;border:1px solid #ffc35c66}}.maker b{{display:block;color:var(--gold);letter-spacing:.15em;font-size:11px}}.maker small{{color:var(--muted);font-size:8px;text-transform:uppercase;letter-spacing:.11em}}.modes{{display:flex;gap:5px;padding:4px;border:1px solid var(--line);border-radius:12px;margin:18px 0;background:#07100c}}.mode{{flex:1;border:0;border-radius:8px;padding:9px;background:transparent;color:var(--muted);font-weight:750}}.mode.active{{background:#17261e;color:var(--ink)}}label{{display:block;margin:14px 0 6px;color:var(--muted);font-size:9px;font-weight:800;letter-spacing:.13em;text-transform:uppercase}}input{{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:#07100c;color:var(--ink);font:inherit}}input:focus{{outline:3px solid #3deba02c;border-color:var(--green)}}button.submit{{width:100%;margin-top:20px;padding:12px;border:0;border-radius:11px;background:linear-gradient(135deg,#ffd47e,#b67921);color:#151008;font-weight:850;cursor:pointer}}button:disabled{{opacity:.55;cursor:wait}}.status{{min-height:22px;margin-top:12px;color:var(--muted);font-size:12px}}.status.error{{color:#ff9a92}}.fine{{margin-top:20px;color:#75847b;font-size:10px}}.hidden{{display:none}}@media(max-width:760px){{.shell{{grid-template-columns:1fr}}.story{{min-height:330px;padding:34px}}.auth{{padding:34px 26px}}h1{{font-size:46px}}}}
</style></head><body><main class="shell"><section class="story"><div class="brand"><span class="sigil">ED</span>EvidenceDesk</div><div class="kicker">Private evidence workspace</div><h1>Answers should earn trust.<span>Start with the source.</span></h1><p class="deck">Upload approved organizational documents, ask in normal language, and inspect the exact evidence behind each supported answer.</p><div class="proof"><span>Private workspace</span><span>Page citations</span><span>Safe refusal</span><span>OCR ready</span></div></section><section class="auth"><div class="maker"><img src="/developer-signature.png" alt="IZAQ developer portrait"><div><b>IZAQ</b><small>Product direction · design × engineering</small></div></div><div><h2 id="title">Welcome back</h2><p id="subtitle" style="color:var(--muted)">{registration_note}</p></div><div class="modes"><button class="mode active" data-mode="login" type="button">Sign in</button>{registration_tab}</div><form id="form"><div id="nameBlock" class="hidden"><label for="name">Your name</label><input id="name" autocomplete="name" maxlength="80"></div><label for="email">Email address</label><input id="email" type="email" autocomplete="email" maxlength="254" required><label for="password">Password</label><input id="password" type="password" autocomplete="current-password" maxlength="128" required><button id="submit" class="submit">Enter EvidenceDesk →</button><div id="status" class="status" role="status" aria-live="polite"></div></form><p class="fine">Sessions use secure HttpOnly cookies. Your workspace is isolated from other users before document retrieval.</p></section></main><script>
const modes=[...document.querySelectorAll('[data-mode]')],nameBlock=document.querySelector('#nameBlock'),form=document.querySelector('#form'),status=document.querySelector('#status'),submit=document.querySelector('#submit');let mode='login';
modes.forEach(button=>button.onclick=()=>{{mode=button.dataset.mode;modes.forEach(item=>item.classList.toggle('active',item===button));let register=mode==='register';nameBlock.classList.toggle('hidden',!register);document.querySelector('#name').required=register;document.querySelector('#password').autocomplete=register?'new-password':'current-password';document.querySelector('#title').textContent=register?'Create your private workspace':'Welcome back';document.querySelector('#subtitle').textContent=register?'Your documents and conversations stay inside your workspace.':'Sign in to continue to your evidence workspace.';submit.textContent=register?'Create workspace →':'Enter EvidenceDesk →'}});
form.onsubmit=async event=>{{event.preventDefault();status.textContent=mode==='register'?'Creating your secure workspace…':'Verifying your account…';status.className='status';submit.disabled=true;let body={{email:document.querySelector('#email').value,password:document.querySelector('#password').value}};if(mode==='register')body.display_name=document.querySelector('#name').value;try{{let response=await fetch('/v1/auth/'+mode,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}}),data=await response.json().catch(()=>({{detail:'Unexpected response'}}));if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Please check the form and try again.');location.replace('/')}}catch(error){{status.textContent=error.message;status.className='status error';submit.disabled=false}}}};
</script></body></html>"""
