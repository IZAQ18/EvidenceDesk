from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from app.config import Classification, Settings, get_settings
from app.db import Database
from app.ingestion import chunk_pages, extract_upload, ocr_capability
from app.models import (
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
from app.repository import DuplicateDocumentError, Repository
from app.security import authenticate_key, enforce_rate_limit, require_admin, require_writer
from app.storage import DocumentStorage
from app.ui import UI_HTML
from app.workflow import AnswerWorkflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("evidencedesk")


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


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    database = Database(resolved_settings.db_path)
    repository = Repository(database)
    storage = DocumentStorage(resolved_settings.document_store_path)
    workflow = AnswerWorkflow(repository)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resolved_settings.validate_auth()
        resolved_settings.resolved_api_keys()
        database.initialize()
        capability = ocr_capability(resolved_settings)
        logger.info(
            "service_started environment=%s ocr_available=%s",
            resolved_settings.env,
            capability["available"],
        )
        yield

    app = FastAPI(
        title="EvidenceDesk",
        version="0.5.0",
        description="A secure evidence workspace with citations, OCR, conversations, and feedback.",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.repository = repository

    def current_settings() -> Settings:
        return resolved_settings

    app.dependency_overrides[get_settings] = current_settings

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "frame-src blob:; frame-ancestors 'none'"
        )
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
        return {"status": "ok", "service": "evidencedesk", "version": "0.5.0"}

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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse(UI_HTML)

    return app


app = create_app()
