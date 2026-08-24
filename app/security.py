from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status

from app.accounts import ACCOUNT_COOKIE, AccountSession
from app.config import Settings, get_settings
from app.models import Principal
from app.pilot import has_pilot_access, pilot_principal

logger = logging.getLogger("evidencedesk.security")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


@lru_cache(maxsize=8)
def _jwks_client(url: str):
    try:
        from jwt import PyJWKClient
    except ImportError as exc:
        raise RuntimeError("Install the OIDC dependencies from requirements.txt") from exc
    return PyJWKClient(url, cache_keys=True, lifespan=300)


def _decode_oidc_token(token: str, settings: Settings) -> Principal:
    try:
        import jwt

        assert settings.oidc_jwks_url
        signing_key = _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
        tenant_id = claims.get(settings.oidc_tenant_claim)
        role = claims.get(settings.oidc_role_claim, "reader")
        clearances = claims.get(settings.oidc_clearances_claim, ["public"])
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Missing tenant claim")
        if role not in {"reader", "editor", "admin"}:
            raise ValueError("Invalid role claim")
        if not isinstance(clearances, list) or not set(clearances) <= {
            "public",
            "internal",
            "confidential",
        }:
            raise ValueError("Invalid clearances claim")
        return Principal(
            subject=str(claims["sub"]),
            tenant_id=tenant_id,
            role=role,
            clearances=clearances,
        )
    except Exception as exc:
        logger.warning("oidc_authentication_denied token_fingerprint=%s", _fingerprint(token))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token"
        ) from exc


def resolve_account_session(request: Request, settings: Settings) -> AccountSession | None:
    if not settings.accounts_enabled:
        return None
    store = getattr(request.app.state, "account_store", None)
    if store is None:
        return None
    return store.resolve(request.cookies.get(ACCOUNT_COOKIE))


def authenticate_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    request: Request = None,
) -> Principal:
    if request and settings.pilot_access_code and has_pilot_access(request, settings):
        return pilot_principal(settings)
    if request:
        account_session = resolve_account_session(request, settings)
        if account_session:
            if request.method.upper() not in _SAFE_METHODS:
                csrf_token = request.headers.get("X-CSRF-Token", "")
                if not hmac.compare_digest(csrf_token, account_session.csrf_token):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Invalid session security token",
                    )
            return account_session.principal
    if authorization and authorization.lower().startswith("bearer "):
        if settings.auth_mode not in {"oidc", "hybrid"}:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token"
            )
        token = authorization.split(" ", 1)[1].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token"
            )
        return _decode_oidc_token(token, settings)
    if settings.auth_mode == "oidc":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    if not x_api_key:
        if settings.accounts_enabled:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    matched = None
    for candidate, principal in settings.resolved_api_keys().items():
        if hmac.compare_digest(x_api_key, candidate):
            matched = principal
    if matched is None:
        logger.warning("authentication_denied key_fingerprint=%s", _fingerprint(x_api_key))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return Principal(**matched.model_dump())


def require_writer(principal: Principal = Depends(authenticate_key)) -> Principal:
    if principal.role not in {"editor", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Writer role required")
    return principal


def require_admin(principal: Principal = Depends(authenticate_key)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return principal


class SlidingWindowRateLimiter:
    """Small single-process limiter; edge throttling replaces this in a distributed deployment."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, identity: str, limit: int) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        with self._lock:
            events = self._events[identity]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


rate_limiter = SlidingWindowRateLimiter()


async def enforce_rate_limit(request: Request, settings: Settings = Depends(get_settings)) -> None:
    identity = (
        request.headers.get("x-api-key")
        or request.headers.get("authorization")
        or request.cookies.get(ACCOUNT_COOKIE)
        or (request.client.host if request.client else "unknown")
    )
    identity = _fingerprint(identity)
    if not rate_limiter.check(identity, settings.rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
