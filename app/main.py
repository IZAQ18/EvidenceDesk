from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.account_export import ExportTooLarge, build_workspace_export
from app.accounts import (
    ACCOUNT_COOKIE,
    AccountDeletionBlocked,
    AccountEmailAction,
    AccountExistsError,
    AccountSession,
    AccountStore,
    InvitationError,
    MembershipError,
    WorkspaceInvitationAction,
    account_page,
    account_recovery_page,
    invitation_page,
)
from app.config import Classification, Settings, get_settings
from app.db import build_database
from app.email_delivery import AccountEmailSender, build_account_email_sender
from app.ingestion import (
    ExtractedDocument,
    ExtractedPage,
    chunk_pages,
    extract_upload,
    ocr_capability,
    validate_upload_bytes,
)
from app.ingestion_worker import run_ingestion_worker, stop_ingestion_worker
from app.model_gateway import ModelGateway, ModelTransport
from app.models import (
    AccountActionOut,
    AccountDeleteRequest,
    AccountEmailRequest,
    AccountLoginRequest,
    AccountOut,
    AccountPasswordResetRequest,
    AccountRegistrationRequest,
    AccountTokenRequest,
    AnswerRequest,
    AnswerResponse,
    BulkDocumentRequest,
    ConversationOut,
    DocumentOut,
    DocumentStatusRequest,
    FeedbackRequest,
    IngestionJobOut,
    MessageOut,
    Principal,
    WorkspaceInvitationAcceptRequest,
    WorkspaceInvitationOut,
    WorkspaceInvitationRequest,
    WorkspaceLeaveRequest,
    WorkspaceMemberOut,
    WorkspaceMemberRemoveRequest,
    WorkspaceMemberUpdateRequest,
    WorkspaceOut,
    WorkspaceOwnershipTransferRequest,
)
from app.payments import InvalidPaymentWebhook, parse_lemonsqueezy_webhook
from app.pilot import (
    PILOT_COOKIE,
    access_page,
    has_pilot_access,
    pilot_cookie_value,
    pilot_principal,
)
from app.public_site import public_page
from app.repository import DuplicateDocumentError, Repository
from app.security import (
    authenticate_key,
    enforce_rate_limit,
    require_admin,
    require_writer,
    resolve_account_session,
)
from app.storage import OriginalStorage, build_document_storage
from app.ui import UI_HTML
from app.workflow import AnswerWorkflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("evidencedesk")
DEMO_DOCUMENTS = Path(__file__).parent / "demo_docs"


def _clean_optional(value: str | None, limit: int = 200) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[\x00-\x1f]", "", value).strip()
    return cleaned[:limit] or None


def _date_value(value: str | None, label: str) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} must use YYYY-MM-DD") from exc


def _seed_demo_documents(
    settings: Settings, repository: Repository, storage: OriginalStorage
) -> None:
    """Seed synthetic examples when an ephemeral pilot starts with an empty database."""
    principal = pilot_principal(settings)
    if repository.list_documents(principal):
        return
    for path in sorted(DEMO_DOCUMENTS.glob("*.md")):
        content = path.read_bytes()
        text = content.decode("utf-8")
        extracted = ExtractedDocument(
            filename=path.name,
            content_type="text/markdown",
            content=content,
            pages=[ExtractedPage(page_number=1, text=text)],
            sha256=hashlib.sha256(content).hexdigest(),
            extraction_method="plain-text",
        )
        document_id = str(uuid4())
        storage_path = storage.save(
            principal.tenant_id,
            document_id,
            ".md",
            content,
            extracted.content_type,
        )
        repository.add_document(
            extracted,
            chunk_pages(extracted.pages),
            "internal",
            "Synthetic People Operations demo",
            principal,
            document_id=document_id,
            storage_path=storage_path,
            collection="Pilot examples",
            version="1.0",
        )
        repository.audit(
            principal,
            "demo_document_seeded",
            "document",
            document_id,
            {"filename": path.name},
        )


def create_app(
    settings: Settings | None = None,
    *,
    model_transport: ModelTransport | None = None,
    account_email_sender: AccountEmailSender | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.validate_model_gateway()
    database = build_database(resolved_settings)
    repository = Repository(
        database,
        semantic_candidate_limit=resolved_settings.semantic_candidate_limit,
        semantic_min_score=resolved_settings.semantic_min_score,
    )
    storage = build_document_storage(resolved_settings)
    model_gateway = ModelGateway(resolved_settings, repository, model_transport)
    workflow = AnswerWorkflow(repository, model_gateway)
    account_store = AccountStore(database, resolved_settings.account_session_hours)
    email_sender = account_email_sender or build_account_email_sender(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        worker_stop = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        try:
            resolved_settings.validate_auth()
            resolved_settings.validate_storage()
            resolved_settings.validate_model_gateway()
            resolved_settings.resolved_api_keys()
            database.initialize()
            if resolved_settings.seed_demo_content:
                _seed_demo_documents(resolved_settings, repository, storage)
            capability = ocr_capability(resolved_settings)
            logger.info(
                "service_started environment=%s ocr_available=%s",
                resolved_settings.env,
                capability["available"],
            )
            if resolved_settings.ingestion_worker_enabled:
                worker_task = asyncio.create_task(
                    run_ingestion_worker(repository, storage, resolved_settings, worker_stop)
                )
            yield
        finally:
            try:
                if worker_task:
                    await stop_ingestion_worker(worker_stop, worker_task)
            finally:
                database.close()

    show_docs = resolved_settings.env != "production" or resolved_settings.expose_api_docs
    app = FastAPI(
        title="EvidenceDesk",
        version="1.0.0",
        description="A secure evidence workspace with citations, OCR, conversations, and feedback.",
        lifespan=lifespan,
        docs_url="/docs" if show_docs else None,
        redoc_url="/redoc" if show_docs else None,
        openapi_url="/openapi.json" if show_docs else None,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=resolved_settings.resolved_allowed_hosts(),
    )
    app.state.settings = resolved_settings
    app.state.repository = repository
    app.state.account_store = account_store
    app.state.account_email_sender = email_sender
    app.state.model_gateway = model_gateway

    def current_settings() -> Settings:
        return resolved_settings

    app.dependency_overrides[get_settings] = current_settings

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        started = time.perf_counter()
        public_paths = {
            "/health",
            "/ready",
            "/robots.txt",
            "/social-card.png",
            "/pilot-background.png",
            "/developer-signature.png",
            "/access",
            "/signin",
            "/forgot-password",
            "/reset-password",
            "/verify-email",
            "/invitation",
            "/v1/auth/register",
            "/v1/auth/login",
            "/v1/auth/verify-email",
            "/v1/auth/verification/resend",
            "/v1/auth/password-reset/request",
            "/v1/auth/password-reset/complete",
            "/v1/auth/invitations/accept",
            "/about",
            "/v1/payments/webhook",
        }
        if (
            resolved_settings.pilot_access_code
            and request.url.path not in public_paths
            and not has_pilot_access(request, resolved_settings)
        ):
            if request.url.path.startswith("/v1/"):
                response = JSONResponse({"detail": "Pilot access required"}, status_code=401)
            else:
                response = RedirectResponse("/access", status_code=307)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        is_public_site = request.url.path == "/about"
        response.headers["X-Robots-Tag"] = (
            "index, follow" if is_public_site else "noindex, nofollow, noarchive"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "public, max-age=300" if is_public_site else "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "frame-src blob:; frame-ancestors 'none'"
        )
        if resolved_settings.env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            with database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        return {"status": "ok", "service": "evidencedesk", "version": "1.0.0"}

    @app.get("/ready")
    def readiness() -> JSONResponse:
        checks = {"database": False, "original_storage": False}
        try:
            checks["database"] = database.ping()
        except Exception as exc:
            logger.warning("readiness_failed dependency=database type=%s", type(exc).__name__)
        try:
            checks["original_storage"] = storage.ping()
        except Exception as exc:
            logger.warning(
                "readiness_failed dependency=original_storage type=%s", type(exc).__name__
            )
        ready = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ready else "unavailable", "checks": checks},
            status_code=200 if ready else 503,
        )

    @app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nAllow: /about\nDisallow: /\n")

    @app.get("/social-card.png", include_in_schema=False)
    def social_card() -> FileResponse:
        return FileResponse(
            Path(__file__).parent / "assets" / "evidencedesk-social-card.png",
            media_type="image/png",
        )

    @app.get("/pilot-background.png", include_in_schema=False)
    def pilot_background() -> FileResponse:
        return FileResponse(
            Path(__file__).parent / "assets" / "evidencedesk-social-background.png",
            media_type="image/png",
        )

    @app.get("/developer-signature.png", include_in_schema=False)
    def developer_signature() -> FileResponse:
        return FileResponse(
            Path(__file__).parent / "assets" / "izaq-developer-signature.png",
            media_type="image/png",
        )

    @app.get("/about", response_class=HTMLResponse, include_in_schema=False)
    def about() -> HTMLResponse:
        return HTMLResponse(
            public_page(
                checkout_url=resolved_settings.checkout_url,
                support_url=resolved_settings.support_url,
                access_url="/signin" if resolved_settings.accounts_enabled else "/access",
            )
        )

    @app.get("/access", response_class=HTMLResponse, response_model=None, include_in_schema=False)
    def pilot_access(request: Request) -> HTMLResponse | RedirectResponse:
        if not resolved_settings.pilot_access_code or has_pilot_access(request, resolved_settings):
            return RedirectResponse("/", status_code=307)
        return HTMLResponse(access_page())

    @app.post("/access", response_class=HTMLResponse, response_model=None, include_in_schema=False)
    def unlock_pilot(access_code: str = Form(...)) -> HTMLResponse | RedirectResponse:
        expected = resolved_settings.pilot_access_code
        if not expected or not hmac.compare_digest(access_code, expected):
            return HTMLResponse(access_page("invalid"), status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            PILOT_COOKIE,
            pilot_cookie_value(expected),
            max_age=resolved_settings.pilot_session_hours * 3600,
            httponly=True,
            secure=resolved_settings.env == "production",
            samesite="lax",
            path="/",
        )
        return response

    def set_account_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            ACCOUNT_COOKIE,
            token,
            max_age=resolved_settings.account_session_hours * 3600,
            httponly=True,
            secure=resolved_settings.env == "production",
            samesite="lax",
            path="/",
        )

    def required_account_session(request: Request) -> AccountSession:
        session = resolve_account_session(request, resolved_settings)
        if not session:
            raise HTTPException(status_code=401, detail="Sign in required")
        return session

    @app.get("/signin", response_class=HTMLResponse, response_model=None, include_in_schema=False)
    def signin(request: Request, next: str = "/") -> HTMLResponse | RedirectResponse:
        next_path = (
            next
            if next.startswith("/") and not next.startswith("//") and "\\" not in next
            else "/"
        )
        if not resolved_settings.accounts_enabled:
            return RedirectResponse("/access" if resolved_settings.pilot_access_code else "/")
        if resolve_account_session(request, resolved_settings):
            return RedirectResponse(next_path, status_code=307)
        return HTMLResponse(
            account_page(
                resolved_settings.account_registration_enabled,
                resolved_settings.account_email_verification_required,
                resolved_settings.account_password_reset_enabled,
                next_path,
            )
        )

    @app.get(
        "/forgot-password",
        response_class=HTMLResponse,
        response_model=None,
        include_in_schema=False,
    )
    def forgot_password_page() -> HTMLResponse | RedirectResponse:
        if not (
            resolved_settings.accounts_enabled and resolved_settings.account_password_reset_enabled
        ):
            return RedirectResponse("/signin", status_code=307)
        return HTMLResponse(account_recovery_page("forgot"))

    @app.get(
        "/reset-password",
        response_class=HTMLResponse,
        response_model=None,
        include_in_schema=False,
    )
    def reset_password_page(token: str = "") -> HTMLResponse | RedirectResponse:
        if not (
            resolved_settings.accounts_enabled and resolved_settings.account_password_reset_enabled
        ):
            return RedirectResponse("/signin", status_code=307)
        return HTMLResponse(account_recovery_page("reset", token[:200]))

    @app.get(
        "/verify-email",
        response_class=HTMLResponse,
        response_model=None,
        include_in_schema=False,
    )
    def verify_email_page(token: str = "") -> HTMLResponse | RedirectResponse:
        if not (
            resolved_settings.accounts_enabled
            and resolved_settings.account_email_verification_required
        ):
            return RedirectResponse("/signin", status_code=307)
        return HTMLResponse(account_recovery_page("verify", token[:200]))

    @app.get(
        "/invitation", response_class=HTMLResponse, response_model=None, include_in_schema=False
    )
    def workspace_invitation_page(token: str = "") -> HTMLResponse | RedirectResponse:
        if not (
            resolved_settings.accounts_enabled and resolved_settings.account_invitations_enabled
        ):
            return RedirectResponse("/signin", status_code=307)
        return HTMLResponse(invitation_page(token[:200]))

    def account_action_url(path: str, token: str) -> str:
        base_url = (resolved_settings.public_base_url or "http://localhost:8000").rstrip("/")
        return f"{base_url}{path}?token={quote(token, safe='')}"

    def deliver_account_email(
        purpose: str, action: AccountEmailAction | WorkspaceInvitationAction
    ) -> bool:
        try:
            if purpose == "verify_email":
                assert isinstance(action, AccountEmailAction)
                email_sender.send_verification(
                    action.email,
                    action.display_name,
                    account_action_url("/verify-email", action.token),
                )
            elif purpose == "reset_password":
                assert isinstance(action, AccountEmailAction)
                email_sender.send_password_reset(
                    action.email,
                    action.display_name,
                    account_action_url("/reset-password", action.token),
                )
            else:
                assert isinstance(action, WorkspaceInvitationAction)
                email_sender.send_invitation(
                    action.email,
                    action.workspace_name,
                    account_action_url("/invitation", action.token),
                )
            return True
        except Exception as exc:
            logger.warning(
                "account_email_delivery_failed purpose=%s type=%s",
                purpose,
                type(exc).__name__,
            )
            return False

    @app.post(
        "/v1/auth/register",
        response_model=AccountOut | AccountActionOut,
        status_code=201,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def register_account(
        payload: AccountRegistrationRequest, response: Response
    ) -> AccountOut | AccountActionOut:
        if not resolved_settings.accounts_enabled:
            raise HTTPException(status_code=404, detail="Account access is not configured")
        if not resolved_settings.account_registration_enabled:
            raise HTTPException(status_code=403, detail="Account registration is invite-only")
        if resolved_settings.account_email_verification_required:
            try:
                action = account_store.register_pending(
                    payload.email,
                    payload.display_name,
                    payload.password.get_secret_value(),
                    resolved_settings.account_verification_token_hours,
                )
            except AccountExistsError:
                action = account_store.issue_verification(
                    payload.email, resolved_settings.account_verification_token_hours
                )
            if action:
                deliver_account_email("verify_email", action)
            response.status_code = 202
            return AccountActionOut(
                message=(
                    "If this address can be registered, a verification link will arrive shortly."
                ),
                action_required="verify_email",
            )
        try:
            session = account_store.register(
                payload.email,
                payload.display_name,
                payload.password.get_secret_value(),
            )
        except AccountExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        set_account_cookie(response, session.token)
        repository.audit(
            session.principal,
            "account_registered",
            "user",
            session.user_id,
            {"workspace_created": True},
        )
        return session.public()

    @app.post(
        "/v1/auth/verification/resend",
        response_model=AccountActionOut,
        status_code=202,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def resend_verification(payload: AccountEmailRequest) -> AccountActionOut:
        if not (
            resolved_settings.accounts_enabled
            and resolved_settings.account_email_verification_required
        ):
            raise HTTPException(status_code=404, detail="Email verification is not configured")
        action = account_store.issue_verification(
            payload.email, resolved_settings.account_verification_token_hours
        )
        if action:
            deliver_account_email("verify_email", action)
        return AccountActionOut(
            message="If this account is awaiting verification, a new link will arrive shortly.",
            action_required="verify_email",
        )

    @app.post(
        "/v1/auth/verify-email",
        response_model=AccountOut,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def verify_email(payload: AccountTokenRequest, response: Response) -> AccountOut:
        if not (
            resolved_settings.accounts_enabled
            and resolved_settings.account_email_verification_required
        ):
            raise HTTPException(status_code=404, detail="Email verification is not configured")
        session = account_store.verify_email(payload.token.get_secret_value())
        if not session:
            raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
        set_account_cookie(response, session.token)
        repository.audit(
            session.principal,
            "account_email_verified",
            "user",
            session.user_id,
        )
        return session.public()

    @app.post(
        "/v1/auth/password-reset/request",
        response_model=AccountActionOut,
        status_code=202,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def request_password_reset(payload: AccountEmailRequest) -> AccountActionOut:
        if not (
            resolved_settings.accounts_enabled and resolved_settings.account_password_reset_enabled
        ):
            raise HTTPException(status_code=404, detail="Password recovery is not configured")
        action = account_store.issue_password_reset(
            payload.email, resolved_settings.account_password_reset_token_minutes
        )
        if action:
            deliver_account_email("reset_password", action)
        return AccountActionOut(
            message="If an active account exists for this address, a reset link will arrive shortly."
        )

    @app.post(
        "/v1/auth/password-reset/complete",
        response_model=AccountActionOut,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def complete_password_reset(
        payload: AccountPasswordResetRequest, response: Response
    ) -> AccountActionOut:
        if not (
            resolved_settings.accounts_enabled and resolved_settings.account_password_reset_enabled
        ):
            raise HTTPException(status_code=404, detail="Password recovery is not configured")
        reset = account_store.reset_password(
            payload.token.get_secret_value(), payload.password.get_secret_value()
        )
        if not reset:
            raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
        response.delete_cookie(
            ACCOUNT_COOKIE,
            httponly=True,
            secure=resolved_settings.env == "production",
            samesite="lax",
            path="/",
        )
        return AccountActionOut(
            message="Password updated. Sign in with your new password.",
            action_required="sign_in",
        )

    @app.post(
        "/v1/auth/login",
        response_model=AccountOut,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def login_account(payload: AccountLoginRequest, response: Response) -> AccountOut:
        if not resolved_settings.accounts_enabled:
            raise HTTPException(status_code=404, detail="Account access is not configured")
        session = account_store.login(payload.email, payload.password.get_secret_value())
        if not session:
            raise HTTPException(status_code=401, detail="Email or password is incorrect")
        set_account_cookie(response, session.token)
        repository.audit(session.principal, "account_login", "user", session.user_id)
        return session.public()

    @app.get("/v1/auth/me", response_model=AccountOut)
    def account_me(request: Request) -> AccountOut:
        session = resolve_account_session(request, resolved_settings)
        if not session:
            raise HTTPException(status_code=401, detail="Sign in required")
        return session.public()

    @app.post(
        "/v1/auth/invitations/accept",
        response_model=AccountOut,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def accept_workspace_invitation(
        payload: WorkspaceInvitationAcceptRequest,
        request: Request,
        response: Response,
    ) -> AccountOut:
        if not (
            resolved_settings.accounts_enabled and resolved_settings.account_invitations_enabled
        ):
            raise HTTPException(status_code=404, detail="Workspace invitations are not configured")
        current = resolve_account_session(request, resolved_settings)
        if current and not hmac.compare_digest(
            request.headers.get("X-CSRF-Token", ""), current.csrf_token
        ):
            raise HTTPException(status_code=403, detail="Invalid session security token")
        try:
            session = account_store.accept_invitation(
                payload.token.get_secret_value(),
                current,
                payload.display_name,
                payload.password.get_secret_value() if payload.password else None,
            )
        except InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        set_account_cookie(response, session.token)
        repository.audit(
            session.principal,
            "workspace_invitation_accepted",
            "user",
            session.user_id,
            {"role": session.role},
        )
        return session.public()

    @app.post(
        "/v1/account/invitations",
        response_model=WorkspaceInvitationOut,
        status_code=201,
    )
    def create_workspace_invitation(
        payload: WorkspaceInvitationRequest,
        request: Request,
        _: Principal = Depends(require_admin),
    ) -> WorkspaceInvitationOut:
        if not resolved_settings.account_invitations_enabled:
            raise HTTPException(status_code=404, detail="Workspace invitations are not configured")
        session = required_account_session(request)
        try:
            action = account_store.create_invitation(
                session,
                payload.email,
                payload.role,
                payload.clearances,
                resolved_settings.account_invitation_hours,
            )
        except InvitationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deliver_account_email("workspace_invitation", action):
            account_store.revoke_invitation(session, action.invitation_id)
            raise HTTPException(status_code=503, detail="Invitation email could not be delivered")
        repository.audit(
            session.principal,
            "workspace_invitation_created",
            "workspace_invitation",
            action.invitation_id,
            {"role": action.role, "clearances": action.clearances},
        )
        return WorkspaceInvitationOut(
            id=action.invitation_id,
            email=action.email,
            role=action.role,
            clearances=action.clearances,
            status="pending",
            expires_at=action.expires_at,
        )

    @app.get(
        "/v1/account/invitations",
        response_model=list[WorkspaceInvitationOut],
    )
    def workspace_invitations(
        request: Request, _: Principal = Depends(require_admin)
    ) -> list[WorkspaceInvitationOut]:
        if not resolved_settings.account_invitations_enabled:
            raise HTTPException(status_code=404, detail="Workspace invitations are not configured")
        session = required_account_session(request)
        return [WorkspaceInvitationOut(**item) for item in account_store.list_invitations(session)]

    @app.delete("/v1/account/invitations/{invitation_id}", status_code=204)
    def revoke_workspace_invitation(
        invitation_id: str,
        request: Request,
        _: Principal = Depends(require_admin),
    ) -> Response:
        if not resolved_settings.account_invitations_enabled:
            raise HTTPException(status_code=404, detail="Workspace invitations are not configured")
        session = required_account_session(request)
        if not account_store.revoke_invitation(session, invitation_id):
            raise HTTPException(status_code=404, detail="Pending invitation not found")
        repository.audit(
            session.principal,
            "workspace_invitation_revoked",
            "workspace_invitation",
            invitation_id,
        )
        return Response(status_code=204)

    @app.get("/v1/account/workspaces", response_model=list[WorkspaceOut])
    def account_workspaces(
        request: Request, _: Principal = Depends(authenticate_key)
    ) -> list[WorkspaceOut]:
        session = required_account_session(request)
        return [WorkspaceOut(**item) for item in account_store.list_workspaces(session)]

    @app.post("/v1/account/workspaces/{workspace_id}/switch", response_model=AccountOut)
    def switch_account_workspace(
        workspace_id: str,
        request: Request,
        response: Response,
        _: Principal = Depends(authenticate_key),
    ) -> AccountOut:
        session = required_account_session(request)
        switched = account_store.switch_workspace(session, workspace_id)
        if not switched:
            raise HTTPException(status_code=404, detail="Workspace membership not found")
        set_account_cookie(response, switched.token)
        repository.audit(
            switched.principal,
            "workspace_switched",
            "workspace",
            switched.workspace_id,
        )
        return switched.public()

    @app.get("/v1/account/members", response_model=list[WorkspaceMemberOut])
    def account_workspace_members(
        request: Request, _: Principal = Depends(require_admin)
    ) -> list[WorkspaceMemberOut]:
        if not resolved_settings.account_invitations_enabled:
            raise HTTPException(status_code=404, detail="Workspace membership is not configured")
        session = required_account_session(request)
        return [WorkspaceMemberOut(**item) for item in account_store.list_members(session)]

    @app.patch("/v1/account/members/{user_id}", response_model=WorkspaceMemberOut)
    def update_account_workspace_member(
        user_id: str,
        payload: WorkspaceMemberUpdateRequest,
        request: Request,
        _: Principal = Depends(require_admin),
    ) -> WorkspaceMemberOut:
        if not resolved_settings.account_invitations_enabled:
            raise HTTPException(status_code=404, detail="Workspace membership is not configured")
        session = required_account_session(request)
        try:
            member = account_store.update_member(
                session, user_id, payload.role, list(payload.clearances)
            )
        except MembershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        repository.audit(
            session.principal,
            "workspace_member_access_updated",
            "user",
            user_id,
            {"role": payload.role, "clearances": payload.clearances},
        )
        return WorkspaceMemberOut(**member)

    @app.post("/v1/account/members/{user_id}/remove", status_code=204)
    def remove_account_workspace_member(
        user_id: str,
        payload: WorkspaceMemberRemoveRequest,
        request: Request,
        _: Principal = Depends(require_admin),
    ) -> Response:
        if not resolved_settings.account_invitations_enabled:
            raise HTTPException(status_code=404, detail="Workspace membership is not configured")
        session = required_account_session(request)
        try:
            account_store.remove_member(
                session, user_id, payload.password.get_secret_value()
            )
        except InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except MembershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        repository.audit(
            session.principal,
            "workspace_member_removed",
            "user",
            user_id,
        )
        return Response(status_code=204)

    @app.post("/v1/account/ownership/transfer", status_code=204)
    def transfer_account_workspace_ownership(
        payload: WorkspaceOwnershipTransferRequest,
        request: Request,
        _: Principal = Depends(require_admin),
    ) -> Response:
        if not resolved_settings.account_invitations_enabled:
            raise HTTPException(status_code=404, detail="Workspace membership is not configured")
        session = required_account_session(request)
        try:
            account_store.transfer_ownership(
                session,
                payload.new_owner_user_id,
                payload.password.get_secret_value(),
            )
        except InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except MembershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        repository.audit(
            session.principal,
            "workspace_ownership_transferred",
            "user",
            payload.new_owner_user_id,
        )
        return Response(status_code=204)

    @app.post("/v1/account/workspaces/current/leave", response_model=AccountOut)
    def leave_current_account_workspace(
        payload: WorkspaceLeaveRequest,
        request: Request,
        response: Response,
        _: Principal = Depends(authenticate_key),
    ) -> AccountOut:
        session = required_account_session(request)
        try:
            switched = account_store.leave_workspace(
                session, payload.password.get_secret_value()
            )
        except InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except MembershipError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        repository.audit(
            session.principal,
            "workspace_member_left",
            "user",
            session.user_id,
        )
        set_account_cookie(response, switched.token)
        return switched.public()

    @app.post("/v1/account/export")
    def export_account_workspace(
        request: Request, _: Principal = Depends(require_admin)
    ) -> Response:
        if not resolved_settings.account_export_enabled:
            raise HTTPException(status_code=404, detail="Workspace export is not configured")
        session = required_account_session(request)
        try:
            archive = build_workspace_export(
                database,
                storage,
                session,
                resolved_settings.account_export_max_bytes,
            )
        except ExportTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        repository.audit(
            session.principal,
            "workspace_export_created",
            "workspace",
            session.workspace_id,
        )
        return Response(
            content=archive,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="evidencedesk-export-{date.today().isoformat()}.zip"'
                )
            },
        )

    @app.post("/v1/account/delete", status_code=204)
    def delete_account(
        payload: AccountDeleteRequest,
        request: Request,
        _: Principal = Depends(authenticate_key),
    ) -> Response:
        if not resolved_settings.account_deletion_enabled:
            raise HTTPException(status_code=404, detail="Account deletion is not configured")
        session = required_account_session(request)
        try:
            originals, staged = account_store.delete_account(
                session, payload.password.get_secret_value()
            )
        except InvitationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AccountDeletionBlocked as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        failures = 0
        for reference in set(originals + staged):
            try:
                storage.delete(reference)
            except Exception as exc:
                failures += 1
                logger.warning("account_storage_cleanup_failed type=%s", type(exc).__name__)
        if failures:
            logger.warning("account_storage_cleanup_incomplete failures=%s", failures)
        response = Response(status_code=204)
        response.delete_cookie(
            ACCOUNT_COOKIE,
            httponly=True,
            secure=resolved_settings.env == "production",
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/v1/auth/logout", status_code=204)
    def logout_account(request: Request, _: Principal = Depends(authenticate_key)) -> Response:
        account_store.logout(request.cookies.get(ACCOUNT_COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(
            ACCOUNT_COOKIE,
            httponly=True,
            secure=resolved_settings.env == "production",
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/v1/payments/webhook", include_in_schema=False)
    async def payment_webhook(request: Request) -> dict[str, bool]:
        configured_secret = resolved_settings.payment_webhook_secret
        if configured_secret is None:
            raise HTTPException(status_code=404, detail="Payment integration is not configured")
        body = await request.body()
        if len(body) > 1_000_000:
            raise HTTPException(status_code=413, detail="Payment event is too large")
        try:
            event = parse_lemonsqueezy_webhook(
                body,
                request.headers.get("X-Signature"),
                configured_secret.get_secret_value(),
            )
        except InvalidPaymentWebhook as exc:
            raise HTTPException(status_code=401, detail="Invalid payment event") from exc
        created = repository.record_payment_event(
            event,
            tenant_id=resolved_settings.pilot_tenant_id,
        )
        logger.info(
            "payment_event provider=lemonsqueezy event=%s stored=%s test_mode=%s",
            event.event_name,
            created,
            event.test_mode,
        )
        return {"accepted": True, "duplicate": not created}

    @app.get("/v1/capabilities", dependencies=[Depends(enforce_rate_limit)])
    def capabilities(_: Principal = Depends(authenticate_key)) -> dict[str, object]:
        return {
            "ocr": ocr_capability(resolved_settings),
            "supported_files": [".txt", ".md", ".pdf"],
            "max_upload_bytes": resolved_settings.max_upload_bytes,
            "max_pdf_pages": resolved_settings.max_pdf_pages,
            "answer_mode": (
                "hybrid-semantic-model-assisted-citation-verified"
                if resolved_settings.model_gateway_enabled
                else "hybrid-semantic-extractive-citation-verified"
            ),
            "retrieval": {
                "mode": "authorized-keyword-plus-local-semantic",
                "encoder": "deterministic-auditable-policy-feature-vectors",
                "neural_embeddings": False,
                "candidate_limit": resolved_settings.semantic_candidate_limit,
                "reranking": True,
            },
            "generation": {
                "enabled": resolved_settings.model_gateway_enabled,
                "mode": "server-managed-with-extractive-fallback"
                if resolved_settings.model_gateway_enabled
                else "local-extractive",
                "provider": resolved_settings.model_provider
                if resolved_settings.model_gateway_enabled
                else None,
                "model": resolved_settings.model_name
                if resolved_settings.model_gateway_enabled
                else None,
                "browser_api_keys": False,
                "external_data": "question-and-authorized-excerpts-only"
                if resolved_settings.model_gateway_enabled
                else "none",
                "response_storage_requested": False,
                "daily_request_limit": resolved_settings.model_daily_request_limit,
                "daily_token_limit": resolved_settings.model_daily_token_limit,
                "daily_cost_limit_cents": resolved_settings.model_daily_cost_limit_cents,
            },
            "auth_mode": resolved_settings.auth_mode,
            "features": [
                "page-evidence",
                "original-preview",
                "conversations",
                "feedback",
                "collections",
                "versions",
                "reports",
                "durable-ingestion-jobs",
                "hybrid-semantic-retrieval",
                "evidence-reranking",
                "retrieval-quality-metrics",
                "server-managed-model-gateway",
                "model-usage-quotas",
                "mechanical-claim-support-checks",
                "automatic-extractive-fallback",
            ],
        }

    @app.post(
        "/v1/ingestion-jobs",
        response_model=IngestionJobOut,
        status_code=202,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def queue_ingestion_job(
        file: UploadFile = File(...),
        classification: Classification = Form(default="internal"),
        source: str | None = Form(default=None, max_length=200),
        collection: str | None = Form(default=None, max_length=100),
        version: str | None = Form(default=None, max_length=50),
        effective_date: str | None = Form(default=None),
        expires_at: str | None = Form(default=None),
        supersedes_document_id: str | None = Form(default=None),
        principal: Principal = Depends(require_writer),
        settings_value: Settings = Depends(current_settings),
    ) -> IngestionJobOut:
        if classification not in principal.clearances:
            raise HTTPException(status_code=403, detail="Classification exceeds caller clearance")
        effective = _date_value(effective_date, "Effective date")
        expiry = _date_value(expires_at, "Expiry date")
        if effective and expiry and expiry < effective:
            raise HTTPException(status_code=422, detail="Expiry date cannot precede effective date")
        if supersedes_document_id and not repository.get_document(
            supersedes_document_id, principal
        ):
            raise HTTPException(status_code=404, detail="Superseded document not found")

        content = await file.read(settings_value.max_upload_bytes + 1)
        filename, extension, supplied_content_type = validate_upload_bytes(
            file.filename, file.content_type, content, settings_value
        )
        content_type = "application/pdf" if extension == ".pdf" else "text/plain"
        document_id = str(uuid4())
        job_id = str(uuid4())
        storage_path = storage.save(
            principal.tenant_id,
            document_id,
            extension,
            content,
            content_type,
        )
        try:
            job = repository.create_ingestion_job(
                job_id=job_id,
                document_id=document_id,
                filename=filename,
                content_type=supplied_content_type,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                classification=classification,
                source=_clean_optional(source),
                collection=_clean_optional(collection, 100),
                version=_clean_optional(version, 50),
                effective_date=effective,
                expires_at=expiry,
                supersedes_document_id=supersedes_document_id,
                storage_path=storage_path,
                principal=principal,
                max_attempts=settings_value.ingestion_job_max_attempts,
            )
        except Exception:
            storage.delete(storage_path)
            raise
        repository.audit(
            principal,
            "ingestion_job_queued",
            "ingestion_job",
            job_id,
            {"filename": filename, "size_bytes": len(content)},
        )
        return IngestionJobOut(**job)

    @app.get(
        "/v1/ingestion-jobs",
        response_model=list[IngestionJobOut],
        dependencies=[Depends(enforce_rate_limit)],
    )
    def list_ingestion_jobs(
        principal: Principal = Depends(authenticate_key),
    ) -> list[IngestionJobOut]:
        return [IngestionJobOut(**job) for job in repository.list_ingestion_jobs(principal)]

    @app.get(
        "/v1/ingestion-jobs/{job_id}",
        response_model=IngestionJobOut,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def get_ingestion_job(
        job_id: str, principal: Principal = Depends(authenticate_key)
    ) -> IngestionJobOut:
        job = repository.get_ingestion_job(job_id, principal)
        if not job:
            raise HTTPException(status_code=404, detail="Ingestion job not found")
        return IngestionJobOut(**job)

    @app.post(
        "/v1/ingestion-jobs/{job_id}/retry",
        response_model=IngestionJobOut,
        status_code=202,
    )
    def retry_ingestion_job(
        job_id: str, principal: Principal = Depends(require_writer)
    ) -> IngestionJobOut:
        if not repository.retry_ingestion_job(job_id, principal):
            raise HTTPException(
                status_code=409,
                detail="Only failed jobs with attempts remaining can be retried",
            )
        job = repository.get_ingestion_job(job_id, principal)
        assert job
        repository.audit(principal, "ingestion_job_retried", "ingestion_job", job_id)
        return IngestionJobOut(**job)

    @app.delete("/v1/ingestion-jobs/{job_id}", status_code=204)
    def delete_ingestion_job(
        job_id: str, principal: Principal = Depends(require_writer)
    ) -> Response:
        deleted, staged_path, _ = repository.delete_ingestion_job(job_id, principal)
        if not deleted:
            raise HTTPException(
                status_code=409,
                detail="The job was not found or is currently processing",
            )
        if staged_path:
            storage.delete(staged_path)
        repository.audit(principal, "ingestion_job_deleted", "ingestion_job", job_id)
        return Response(status_code=204)

    @app.post(
        "/v1/documents",
        response_model=DocumentOut,
        status_code=201,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def ingest_document(
        file: UploadFile = File(...),
        classification: Classification = Form(default="internal"),
        source: str | None = Form(default=None, max_length=200),
        collection: str | None = Form(default=None, max_length=100),
        version: str | None = Form(default=None, max_length=50),
        effective_date: str | None = Form(default=None),
        expires_at: str | None = Form(default=None),
        supersedes_document_id: str | None = Form(default=None),
        principal: Principal = Depends(require_writer),
        settings_value: Settings = Depends(current_settings),
    ) -> DocumentOut:
        if classification not in principal.clearances:
            raise HTTPException(status_code=403, detail="Classification exceeds caller clearance")
        effective = _date_value(effective_date, "Effective date")
        expiry = _date_value(expires_at, "Expiry date")
        if effective and expiry and expiry < effective:
            raise HTTPException(status_code=422, detail="Expiry date cannot precede effective date")
        if supersedes_document_id and not repository.get_document(
            supersedes_document_id, principal
        ):
            raise HTTPException(status_code=404, detail="Superseded document not found")

        extracted = await extract_upload(file, settings_value)
        chunks = chunk_pages(extracted.pages)
        if len(chunks) > settings_value.max_chunks_per_document:
            raise HTTPException(status_code=413, detail="Document creates too many chunks")
        document_id = str(uuid4())
        extension = Path(extracted.filename).suffix
        storage_path = storage.save(
            principal.tenant_id,
            document_id,
            extension,
            extracted.content,
            extracted.content_type,
        )
        try:
            repository.add_document(
                extracted,
                chunks,
                classification,
                _clean_optional(source),
                principal,
                document_id=document_id,
                storage_path=storage_path,
                collection=_clean_optional(collection, 100),
                version=_clean_optional(version, 50),
                effective_date=effective,
                expires_at=expiry,
                supersedes_document_id=supersedes_document_id,
            )
        except DuplicateDocumentError as exc:
            storage.delete(storage_path)
            raise HTTPException(
                status_code=409,
                detail="An identical document already exists at this classification",
            ) from exc
        except Exception:
            storage.delete(storage_path)
            raise
        repository.audit(
            principal,
            "document_ingested",
            "document",
            document_id,
            {"pages": extracted.page_count, "method": extracted.extraction_method},
        )
        documents = repository.list_documents(principal)
        return DocumentOut(**next(item for item in documents if item["id"] == document_id))

    @app.get(
        "/v1/documents",
        response_model=list[DocumentOut],
        dependencies=[Depends(enforce_rate_limit)],
    )
    def list_documents(
        q: str | None = None,
        classification: Classification | None = None,
        status: str | None = None,
        collection: str | None = None,
        principal: Principal = Depends(authenticate_key),
    ) -> list[DocumentOut]:
        documents = repository.list_documents(principal)
        if q:
            needle = q.lower().strip()
            documents = [
                item
                for item in documents
                if needle
                in f"{item['filename']} {item.get('source') or ''} {item.get('collection') or ''}".lower()
            ]
        if classification:
            documents = [item for item in documents if item["classification"] == classification]
        if status:
            documents = [item for item in documents if item["status"] == status]
        if collection:
            documents = [item for item in documents if item.get("collection") == collection]
        return [DocumentOut(**item) for item in documents]

    @app.get("/v1/documents/{document_id}/content", dependencies=[Depends(enforce_rate_limit)])
    def document_content(
        document_id: str, principal: Principal = Depends(authenticate_key)
    ) -> StreamingResponse:
        document = repository.get_document(document_id, principal)
        if not document or not document.get("storage_path"):
            raise HTTPException(status_code=404, detail="Original document not found")
        try:
            content = storage.stream(document["storage_path"])
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Original document not found") from exc
        return StreamingResponse(
            content,
            media_type=document["content_type"],
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(document['filename'])}"
            },
        )

    @app.get("/v1/documents/{document_id}/pages/{page_number}")
    def document_page(
        document_id: str,
        page_number: int,
        principal: Principal = Depends(authenticate_key),
    ) -> dict:
        page = repository.get_page(document_id, page_number, principal)
        if not page:
            raise HTTPException(status_code=404, detail="Evidence page not found")
        return page

    @app.get("/v1/documents/{document_id}/suggestions")
    def document_suggestions(
        document_id: str, principal: Principal = Depends(authenticate_key)
    ) -> dict[str, list[str]]:
        page = repository.get_page(document_id, 1, principal)
        if not page:
            raise HTTPException(status_code=404, detail="Document not found")
        headings = [
            re.sub(r"^#+\s*", "", line).strip(" :")
            for line in page["text"].splitlines()
            if line.strip().startswith("#") or (3 <= len(line.split()) <= 8 and len(line) < 90)
        ]
        headings = list(dict.fromkeys(item for item in headings if len(item) >= 4))[:4]
        questions = [f"What does {page['filename']} say about {heading}?" for heading in headings]
        if not questions:
            questions = [
                f"What are the main requirements in {page['filename']}?",
                f"What actions or deadlines are described in {page['filename']}?",
            ]
        return {"questions": questions}

    @app.patch("/v1/documents/{document_id}/status", response_model=DocumentOut)
    def update_document_status(
        document_id: str,
        payload: DocumentStatusRequest,
        principal: Principal = Depends(require_writer),
    ) -> DocumentOut:
        if not repository.set_document_status(document_id, payload.status, principal):
            raise HTTPException(status_code=404, detail="Document not found")
        repository.audit(principal, f"document_{payload.status}", "document", document_id)
        document = repository.get_document(document_id, principal)
        listed = next(
            item for item in repository.list_documents(principal) if item["id"] == document_id
        )
        assert document is not None
        return DocumentOut(**listed)

    @app.delete(
        "/v1/documents/{document_id}",
        status_code=204,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def delete_document(document_id: str, principal: Principal = Depends(require_admin)) -> None:
        deleted, storage_path = repository.delete_document(document_id, principal)
        if not deleted:
            raise HTTPException(status_code=404, detail="Document not found")
        storage.delete(storage_path)
        repository.audit(principal, "document_deleted", "document", document_id)

    @app.post("/v1/documents/bulk")
    def bulk_documents(
        payload: BulkDocumentRequest, principal: Principal = Depends(require_admin)
    ) -> dict[str, int]:
        affected = 0
        for document_id in dict.fromkeys(payload.document_ids):
            if payload.action == "delete":
                deleted, storage_path = repository.delete_document(document_id, principal)
                if deleted:
                    storage.delete(storage_path)
                    affected += 1
            else:
                target_status = "archived" if payload.action == "archive" else "active"
                affected += int(
                    repository.set_document_status(document_id, target_status, principal)
                )
        repository.audit(
            principal,
            f"bulk_{payload.action}",
            "document",
            None,
            {"requested": len(payload.document_ids), "affected": affected},
        )
        return {"affected": affected}

    @app.post(
        "/v1/answers",
        response_model=AnswerResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def answer(
        payload: AnswerRequest, principal: Principal = Depends(authenticate_key)
    ) -> AnswerResponse:
        document_ids = list(dict.fromkeys(payload.document_ids))
        try:
            conversation_id = repository.create_or_get_conversation(
                principal, payload.question, payload.conversation_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result = workflow.run(payload.question, principal, payload.top_k, document_ids)
        message_id = repository.record_exchange(
            principal,
            conversation_id,
            payload.question,
            result.answer,
            result.grounded,
            result.evidence_quality,
            [citation.model_dump(mode="json") for citation in result.citations],
            [step.model_dump(mode="json") for step in result.workflow],
        )
        if result.retrieval:
            repository.record_answer_diagnostics(
                principal, message_id, result.retrieval, result.grounded
            )
        result.conversation_id = conversation_id
        result.message_id = message_id
        repository.audit(
            principal,
            "answer_completed",
            "message",
            message_id,
            {"grounded": result.grounded, "quality": result.evidence_quality},
        )
        return result

    @app.get("/v1/conversations", response_model=list[ConversationOut])
    def conversations(
        principal: Principal = Depends(authenticate_key),
    ) -> list[ConversationOut]:
        return [ConversationOut(**item) for item in repository.list_conversations(principal)]

    @app.get("/v1/conversations/{conversation_id}/messages", response_model=list[MessageOut])
    def messages(
        conversation_id: str, principal: Principal = Depends(authenticate_key)
    ) -> list[MessageOut]:
        result = repository.get_messages(conversation_id, principal)
        if result is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return [MessageOut(**item) for item in result]

    @app.delete("/v1/conversations/{conversation_id}", status_code=204)
    def delete_conversation(
        conversation_id: str, principal: Principal = Depends(authenticate_key)
    ) -> None:
        if not repository.delete_conversation(conversation_id, principal):
            raise HTTPException(status_code=404, detail="Conversation not found")

    @app.post("/v1/feedback", status_code=201)
    def feedback(
        payload: FeedbackRequest, principal: Principal = Depends(authenticate_key)
    ) -> dict[str, str]:
        comment = _clean_optional(payload.comment, 500)
        if not repository.add_feedback(principal, payload.message_id, payload.category, comment):
            raise HTTPException(status_code=404, detail="Answer message not found")
        repository.audit(
            principal,
            "feedback_recorded",
            "message",
            payload.message_id,
            {"category": payload.category},
        )
        return {"status": "recorded"}

    @app.get("/v1/messages/{message_id}/report", response_class=PlainTextResponse)
    def answer_report(
        message_id: str, principal: Principal = Depends(authenticate_key)
    ) -> PlainTextResponse:
        data = repository.report_data(message_id, principal)
        if not data:
            raise HTTPException(status_code=404, detail="Answer message not found")
        lines = [
            "# EvidenceDesk evidence report",
            "",
            f"**Question:** {data['question']}",
            "",
            f"**Evidence quality:** {data['evidence_quality'] or 'unknown'}",
            "",
            "## Answer",
            "",
            data["content"],
            "",
            "## Sources",
            "",
        ]
        for citation in data["citations"]:
            page = f", page {citation['page_number']}" if citation.get("page_number") else ""
            lines.extend(
                [
                    f"{citation['number']}. **{citation['filename']}**{page}",
                    f"   - {citation['excerpt']}",
                ]
            )
        lines.extend(
            [
                "",
                "---",
                "Generated from stored organizational evidence. Verify citations before consequential use.",
            ]
        )
        return PlainTextResponse(
            "\n".join(lines),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="evidence-{message_id[:8]}.md"'},
        )

    @app.get("/v1/admin/metrics")
    def admin_metrics(principal: Principal = Depends(require_admin)) -> dict:
        return repository.admin_metrics(principal)

    @app.get("/", response_class=HTMLResponse, response_model=None, include_in_schema=False)
    def index(request: Request) -> HTMLResponse | RedirectResponse:
        if resolved_settings.accounts_enabled and not resolve_account_session(
            request, resolved_settings
        ):
            return RedirectResponse("/signin", status_code=307)
        base_url = (resolved_settings.public_base_url or str(request.base_url)).rstrip("/")
        html = UI_HTML.replace("__SOCIAL_CARD_URL__", f"{base_url}/social-card.png")
        html = html.replace(
            "__PILOT_MODE__", "true" if resolved_settings.pilot_access_code else "false"
        )
        html = html.replace(
            "__ACCOUNT_MODE__", "true" if resolved_settings.accounts_enabled else "false"
        )
        html = html.replace(
            "__ACCOUNT_INVITES__",
            "true" if resolved_settings.account_invitations_enabled else "false",
        )
        html = html.replace(
            "__ACCOUNT_EXPORT__",
            "true" if resolved_settings.account_export_enabled else "false",
        )
        html = html.replace(
            "__ACCOUNT_DELETE__",
            "true" if resolved_settings.account_deletion_enabled else "false",
        )
        html = html.replace("__SUPPORT_URL__", json.dumps(resolved_settings.support_url or ""))
        html = html.replace("__CHECKOUT_URL__", json.dumps(resolved_settings.checkout_url or ""))
        return HTMLResponse(html)

    return app


app = create_app()
