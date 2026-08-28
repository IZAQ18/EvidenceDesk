from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from app.config import Settings

GOOGLE_OAUTH_COOKIE = "evidencedesk_google_oauth"
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
OAUTH_ATTEMPT_SECONDS = 600


class GoogleOAuthError(ValueError):
    """A safe, user-facing Google authentication failure category."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    display_name: str


@dataclass(frozen=True)
class GoogleOAuthResult:
    identity: GoogleIdentity
    next_path: str


class GoogleOAuthProvider(Protocol):
    enabled: bool

    def begin(self, next_path: str) -> tuple[str, str]: ...

    async def complete(self, code: str, state: str, attempt_cookie: str) -> GoogleOAuthResult: ...


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _safe_next_path(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value[:500]
    return "/"


@lru_cache(maxsize=2)
def _google_jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=10)


class GoogleOAuthClient:
    """Minimal Google OpenID Connect adapter for EvidenceDesk browser sessions."""

    enabled: bool

    def __init__(self, settings: Settings):
        self.enabled = settings.google_oauth_enabled
        self.client_id = (settings.google_oauth_client_id or "").strip()
        self.client_secret = (
            settings.google_oauth_client_secret.get_secret_value().strip()
            if settings.google_oauth_client_secret
            else ""
        )
        self.redirect_uri = settings.resolved_google_oauth_redirect_uri()
        self.timeout = settings.google_oauth_timeout_seconds

    def _sign(self, payload: str) -> str:
        return _b64encode(
            hmac.new(self.client_secret.encode(), payload.encode(), hashlib.sha256).digest()
        )

    def _encode_attempt(self, values: dict[str, object]) -> str:
        payload = _b64encode(json.dumps(values, separators=(",", ":")).encode())
        return f"{payload}.{self._sign(payload)}"

    def _decode_attempt(self, value: str) -> dict[str, object]:
        try:
            if not value or len(value) > 5_000:
                raise GoogleOAuthError("state_invalid")
            payload, supplied_signature = value.split(".", 1)
            if not hmac.compare_digest(supplied_signature, self._sign(payload)):
                raise GoogleOAuthError("state_invalid")
            decoded = json.loads(_b64decode(payload))
            issued_at = int(decoded["issued_at"])
            now = int(time.time())
            if issued_at > now + 60 or now - issued_at > OAUTH_ATTEMPT_SECONDS:
                raise GoogleOAuthError("state_expired")
            for name, minimum, maximum in (
                ("state", 32, 100),
                ("nonce", 32, 100),
                ("code_verifier", 43, 128),
            ):
                item = decoded.get(name)
                if not isinstance(item, str) or not minimum <= len(item) <= maximum:
                    raise GoogleOAuthError("state_invalid")
            return decoded
        except GoogleOAuthError:
            raise
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise GoogleOAuthError("state_invalid") from exc

    def begin(self, next_path: str) -> tuple[str, str]:
        if not self.enabled:
            raise GoogleOAuthError("not_configured")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = _b64encode(hashlib.sha256(code_verifier.encode()).digest())
        attempt_cookie = self._encode_attempt(
            {
                "state": state,
                "nonce": nonce,
                "code_verifier": code_verifier,
                "next_path": _safe_next_path(next_path),
                "issued_at": int(time.time()),
            }
        )
        parameters = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "prompt": "select_account",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        authorization_url = f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(parameters)}"
        return authorization_url, attempt_cookie

    async def complete(self, code: str, state: str, attempt_cookie: str) -> GoogleOAuthResult:
        if not self.enabled:
            raise GoogleOAuthError("not_configured")
        attempt = self._decode_attempt(attempt_cookie)
        expected_state = str(attempt.get("state", ""))
        if not state or not hmac.compare_digest(state, expected_state):
            raise GoogleOAuthError("state_invalid")
        if not code or len(code) > 4096:
            raise GoogleOAuthError("code_invalid")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "code": code,
                        "code_verifier": str(attempt.get("code_verifier", "")),
                        "grant_type": "authorization_code",
                        "redirect_uri": self.redirect_uri,
                    },
                )
            if response.status_code != 200:
                raise GoogleOAuthError("exchange_failed")
            token_payload = response.json()
            id_token = token_payload.get("id_token")
            if not isinstance(id_token, str) or len(id_token) > 20_000:
                raise GoogleOAuthError("identity_missing")
        except GoogleOAuthError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise GoogleOAuthError("exchange_failed") from exc
        identity = await asyncio.to_thread(
            self._verify_id_token,
            id_token,
            str(attempt.get("nonce", "")),
        )
        return GoogleOAuthResult(
            identity=identity,
            next_path=_safe_next_path(str(attempt.get("next_path", "/"))),
        )

    def _verify_id_token(self, encoded: str, expected_nonce: str) -> GoogleIdentity:
        try:
            header = jwt.get_unverified_header(encoded)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise GoogleOAuthError("identity_invalid")
            signing_key = _google_jwks_client(GOOGLE_JWKS_URL).get_signing_key_from_jwt(encoded)
            claims = jwt.decode(
                encoded,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=GOOGLE_ISSUERS,
                options={"require": ["exp", "iat", "sub", "aud", "email", "nonce"]},
            )
            if not hmac.compare_digest(str(claims.get("nonce", "")), expected_nonce):
                raise GoogleOAuthError("identity_invalid")
            if claims.get("email_verified") is not True:
                raise GoogleOAuthError("email_unverified")
            authorized_party = claims.get("azp")
            if authorized_party and not hmac.compare_digest(str(authorized_party), self.client_id):
                raise GoogleOAuthError("identity_invalid")
            subject = str(claims["sub"]).strip()
            email = str(claims["email"]).strip().lower()
            display_name = " ".join(str(claims.get("name") or email.split("@", 1)[0]).split())
            if not subject or len(subject) > 255 or len(email) > 254 or "@" not in email:
                raise GoogleOAuthError("identity_invalid")
            if not display_name or any(ord(character) < 32 for character in display_name):
                display_name = "Google user"
            return GoogleIdentity(subject, email, display_name[:80])
        except GoogleOAuthError:
            raise
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise GoogleOAuthError("identity_invalid") from exc
