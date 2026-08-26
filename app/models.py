from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

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


class AccountActionOut(BaseModel):
    accepted: bool = True
    message: str
    action_required: Literal["verify_email", "sign_in"] | None = None


class AccountEmailRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return AccountRegistrationRequest.normalize_email(value)


class AccountTokenRequest(BaseModel):
    token: SecretStr = Field(min_length=20, max_length=200)


class AccountPasswordResetRequest(AccountTokenRequest):
    password: SecretStr = Field(min_length=12, max_length=128)


class WorkspaceInvitationRequest(AccountEmailRequest):
    role: Literal["reader", "editor", "admin"] = "reader"
    clearances: list[Classification] = Field(default_factory=lambda: ["public", "internal"])

    @field_validator("clearances")
    @classmethod
    def unique_clearances(cls, value: list[Classification]) -> list[Classification]:
        if not value:
            raise ValueError("Choose at least one classification clearance")
        return list(dict.fromkeys(value))


class WorkspaceInvitationAcceptRequest(AccountTokenRequest):
    display_name: str | None = Field(default=None, min_length=2, max_length=80)
    password: SecretStr | None = Field(default=None, min_length=12, max_length=128)

    @model_validator(mode="after")
    def complete_new_account_fields(self) -> WorkspaceInvitationAcceptRequest:
        if (self.display_name is None) != (self.password is None):
            raise ValueError("Display name and password must be provided together")
        if self.display_name is not None:
            self.display_name = AccountRegistrationRequest.clean_display_name(self.display_name)
        return self


class WorkspaceOut(BaseModel):
    id: str
    name: str
    plan: str
    role: Literal["reader", "editor", "admin"]
    clearances: list[Classification]
    created_by: str
    member_count: int


class WorkspaceMemberOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: Literal["reader", "editor", "admin"]
    clearances: list[Classification]
    joined_at: datetime
    is_owner: bool


class WorkspaceMemberUpdateRequest(BaseModel):
    role: Literal["reader", "editor", "admin"]
    clearances: list[Classification]

    @field_validator("clearances")
    @classmethod
    def unique_member_clearances(cls, value: list[Classification]) -> list[Classification]:
        if not value:
            raise ValueError("Choose at least one classification clearance")
        return list(dict.fromkeys(value))


class WorkspaceMemberRemoveRequest(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=6, max_length=20)

    @field_validator("confirmation")
    @classmethod
    def validate_confirmation(cls, value: str) -> str:
        if value.strip() != "REMOVE":
            raise ValueError("Type REMOVE to confirm")
        return "REMOVE"


class WorkspaceOwnershipTransferRequest(BaseModel):
    new_owner_user_id: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=8, max_length=20)

    @field_validator("confirmation")
    @classmethod
    def validate_confirmation(cls, value: str) -> str:
        if value.strip() != "TRANSFER":
            raise ValueError("Type TRANSFER to confirm")
        return "TRANSFER"


class WorkspaceLeaveRequest(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=5, max_length=20)

    @field_validator("confirmation")
    @classmethod
    def validate_confirmation(cls, value: str) -> str:
        if value.strip() != "LEAVE":
            raise ValueError("Type LEAVE to confirm")
        return "LEAVE"


class WorkspaceInvitationOut(BaseModel):
    id: str
    email: str
    role: Literal["reader", "editor", "admin"]
    clearances: list[Classification]
    status: Literal["pending", "accepted", "revoked", "expired"]
    expires_at: datetime


class AccountDeleteRequest(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=6, max_length=20)

    @field_validator("confirmation")
    @classmethod
    def validate_confirmation(cls, value: str) -> str:
        if value.strip() != "DELETE":
            raise ValueError("Type DELETE to confirm")
        return "DELETE"


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
    stage: Literal[
        "security_check",
        "scanning",
        "queued",
        "extracting",
        "ocr",
        "indexing",
        "ready",
        "failed",
        "quarantined",
        "dead_letter",
    ]
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


class RetrievalSummary(BaseModel):
    mode: str
    authorized_candidates: int = 0
    lexical_hits: int = 0
    semantic_hits: int = 0
    returned_chunks: int = 0
    best_semantic_score: float = 0.0
    best_rerank_score: float = 0.0
    duration_ms: float = 0.0


class GenerationSummary(BaseModel):
    mode: Literal["extractive", "model", "extractive_fallback"] = "extractive"
    provider: str | None = None
    model: str | None = None
    usage_event_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_microusd: int = 0
    latency_ms: float = 0.0
    fallback_reason: str | None = None


class AnswerResponse(BaseModel):
    answer: str
    grounded: bool
    citations: list[Citation]
    workflow: list[WorkflowStep]
    warning: str | None = None
    evidence_quality: Literal["strong", "partial", "insufficient"] = "insufficient"
    quality_reason: str = "No direct evidence was available."
    retrieval: RetrievalSummary | None = None
    generation: GenerationSummary = Field(default_factory=GenerationSummary)
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
