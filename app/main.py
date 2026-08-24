from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.accounts import ACCOUNT_COOKIE, AccountExistsError, AccountStore, account_page
from app.config import Classification, Settings, get_settings
from app.db import Database
from app.ingestion import (
    ExtractedDocument,
    ExtractedPage,
    chunk_pages,
    extract_upload,
    ocr_capability,
)
from app.models import (
    AccountLoginRequest,
    AccountOut,
    AccountRegistrationRequest,
    AnswerRequest,
    AnswerResponse,
    BulkDocumentRequest,
    ConversationOut,
    DocumentOut,
    DocumentStatusRequest,
    FeedbackRequest,
    MessageOut,
    Principal,
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
from app.storage import DocumentStorage
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
    settings: Settings, repository: Repository, storage: DocumentStorage
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
        storage_path = storage.save(principal.tenant_id, document_id, ".md", content)
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


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    database = Database(resolved_settings.db_path)
    repository = Repository(database)
    storage = DocumentStorage(resolved_settings.document_store_path)
    workflow = AnswerWorkflow(repository)
    account_store = AccountStore(database, resolved_settings.account_session_hours)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resolved_settings.validate_auth()
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
        yield

    show_docs = resolved_settings.env != "production" or resolved_settings.expose_api_docs
    app = FastAPI(
        title="EvidenceDesk",
        version="0.9.0",
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

    def current_settings() -> Settings:
        return resolved_settings

    app.dependency_overrides[get_settings] = current_settings

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        started = time.perf_counter()
        public_paths = {
            "/health",
            "/robots.txt",
            "/social-card.png",
            "/pilot-background.png",
            "/developer-signature.png",
            "/access",
            "/signin",
            "/v1/auth/register",
            "/v1/auth/login",
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
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        return {"status": "ok", "service": "evidencedesk", "version": "0.9.0"}

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

    @app.get(
        "/access", response_class=HTMLResponse, response_model=None, include_in_schema=False
    )
    def pilot_access(request: Request) -> HTMLResponse | RedirectResponse:
        if not resolved_settings.pilot_access_code or has_pilot_access(request, resolved_settings):
            return RedirectResponse("/", status_code=307)
        return HTMLResponse(access_page())

    @app.post(
        "/access", response_class=HTMLResponse, response_model=None, include_in_schema=False
    )
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

    @app.get(
        "/signin", response_class=HTMLResponse, response_model=None, include_in_schema=False
    )
    def signin(request: Request) -> HTMLResponse | RedirectResponse:
        if not resolved_settings.accounts_enabled:
            return RedirectResponse("/access" if resolved_settings.pilot_access_code else "/")
        if resolve_account_session(request, resolved_settings):
            return RedirectResponse("/", status_code=307)
        return HTMLResponse(account_page(resolved_settings.account_registration_enabled))

    @app.post(
        "/v1/auth/register",
        response_model=AccountOut,
        status_code=201,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def register_account(payload: AccountRegistrationRequest, response: Response) -> AccountOut:
        if not resolved_settings.accounts_enabled:
            raise HTTPException(status_code=404, detail="Account access is not configured")
        if not resolved_settings.account_registration_enabled:
            raise HTTPException(status_code=403, detail="Account registration is invite-only")
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

    @app.post("/v1/auth/logout", status_code=204)
    def logout_account(
        request: Request, _: Principal = Depends(authenticate_key)
    ) -> Response:
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
            "answer_mode": "hybrid-extractive-citation-verified",
            "auth_mode": resolved_settings.auth_mode,
            "features": [
                "page-evidence",
                "original-preview",
                "conversations",
                "feedback",
                "collections",
                "versions",
                "reports",
            ],
        }

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
        if supersedes_document_id and not repository.get_document(supersedes_document_id, principal):
            raise HTTPException(status_code=404, detail="Superseded document not found")

        extracted = await extract_upload(file, settings_value)
        chunks = chunk_pages(extracted.pages)
        if len(chunks) > settings_value.max_chunks_per_document:
            raise HTTPException(status_code=413, detail="Document creates too many chunks")
        document_id = str(uuid4())
        extension = Path(extracted.filename).suffix
        storage_path = storage.save(
            principal.tenant_id, document_id, extension, extracted.content
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
                if needle in f"{item['filename']} {item.get('source') or ''} {item.get('collection') or ''}".lower()
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
    ) -> FileResponse:
        document = repository.get_document(document_id, principal)
        if not document or not document.get("storage_path"):
            raise HTTPException(status_code=404, detail="Original document not found")
        try:
            path = storage.path_for(document["storage_path"])
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Original document not found") from exc
        return FileResponse(
            path,
            media_type=document["content_type"],
            filename=document["filename"],
            content_disposition_type="inline",
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
        listed = next(item for item in repository.list_documents(principal) if item["id"] == document_id)
        assert document is not None
        return DocumentOut(**listed)

    @app.delete(
        "/v1/documents/{document_id}",
        status_code=204,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def delete_document(
        document_id: str, principal: Principal = Depends(require_admin)
    ) -> None:
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
        if not repository.add_feedback(
            principal, payload.message_id, payload.category, comment
        ):
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

    @app.get(
        "/", response_class=HTMLResponse, response_model=None, include_in_schema=False
    )
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
            "__SUPPORT_URL__", json.dumps(resolved_settings.support_url or "")
        )
        html = html.replace(
            "__CHECKOUT_URL__", json.dumps(resolved_settings.checkout_url or "")
        )
        return HTMLResponse(html)

    return app


app = create_app()
