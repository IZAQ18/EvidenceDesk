from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.config import Classification


class Principal(BaseModel):
    subject: str
    tenant_id: str
    role: str
    clearances: list[Classification]


class DocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    source: str | None
    classification: Classification
    created_at: datetime
    created_by: str
    chunk_count: int
    page_count: int
    word_count: int
    extraction_method: str
    extraction_warning: str | None = None
    collection: str | None = None
    version: str | None = None
    effective_date: date | None = None
    expires_at: date | None = None
    status: Literal["active", "archived", "superseded"] = "active"
    has_original: bool = False


class AnswerRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1_000)
    top_k: int = Field(default=4, ge=1, le=10)
    document_ids: list[str] = Field(default_factory=list, max_length=20)
    conversation_id: str | None = None


class Citation(BaseModel):
    number: int
    document_id: str
    chunk_id: int
    filename: str
    source: str | None
    classification: Classification
    excerpt: str
    page_number: int | None = None


class WorkflowStep(BaseModel):
    name: str
    status: str
    detail: str


class AnswerResponse(BaseModel):
    answer: str
    grounded: bool
    citations: list[Citation]
    workflow: list[WorkflowStep]
    warning: str | None = None
    evidence_quality: Literal["strong", "partial", "insufficient"] = "insufficient"
    quality_reason: str = "No direct evidence was available."
    conversation_id: str | None = None
    message_id: str | None = None


class DocumentStatusRequest(BaseModel):
    status: Literal["active", "archived"]


class BulkDocumentRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=50)
    action: Literal["archive", "restore", "delete"]


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    grounded: bool | None = None
    evidence_quality: str | None = None
    citations: list[Citation] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    message_id: str
    category: Literal["helpful", "unsupported", "wrong_source", "missing_information", "ocr_error"]
    comment: str | None = Field(default=None, max_length=500)
