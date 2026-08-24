from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.config import Classification


class Principal(BaseModel):
    subject: str
    tenant_id: str
    role: str
    clearances: list[Classification]


class AccountRegistrationRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=254)
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2 or any(ord(character) < 32 for character in cleaned):
            raise ValueError("Enter a valid display name")
        return cleaned

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        local, separator, domain = normalized.rpartition("@")
        if (
            not separator
            or not local
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("Enter a valid email address")
        return normalized


class AccountLoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class AccountOut(BaseModel):
    id: str
    email: str
    display_name: str
    workspace_id: str
    workspace_name: str
    role: Literal["reader", "editor", "admin"]
    clearances: list[Classification]
    plan: str
    csrf_token: str


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


class IngestionJobOut(BaseModel):
    id: str
    document_id: str
    filename: str
    classification: Classification
    status: Literal["queued", "processing", "ready", "failed"]
    stage: Literal["security_check", "queued", "extracting", "ocr", "indexing", "ready", "failed"]
    progress: int = Field(ge=0, le=100)
    detail: str
    attempts: int
    max_attempts: int
    error_code: str | None = None
    error_message: str | None = None
    error_action: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


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
