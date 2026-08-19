from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Classification = Literal["public", "internal", "confidential"]
CLASSIFICATION_LEVEL: dict[str, int] = {"public": 0, "internal": 1, "confidential": 2}


class PrincipalConfig(BaseModel):
    subject: str
    tenant_id: str
    role: Literal["reader", "editor", "admin"] = "reader"
    clearances: list[Classification] = Field(default_factory=lambda: ["public"])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVIDENCEDESK_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    auth_mode: Literal["api_key", "oidc", "hybrid"] = "api_key"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_tenant_claim: str = "tenant_id"
    oidc_role_claim: str = "role"
    oidc_clearances_claim: str = "clearances"
    db_path: Path = Path("data/evidencedesk.db")
    document_store_path: Path = Path("data/originals")
    max_upload_bytes: int = Field(default=10_000_000, ge=1, le=50_000_000)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    max_chunks_per_document: int = Field(default=2_000, ge=1, le=20_000)
    max_pdf_pages: int = Field(default=100, ge=1, le=500)
    ocr_enabled: bool = True
    ocr_languages: str = Field(default="eng", pattern=r"^[a-zA-Z0-9_+.-]+$")
    ocr_dpi: int = Field(default=200, ge=100, le=300)
    ocr_max_pages: int = Field(default=30, ge=1, le=100)
    ocr_min_words_per_page: int = Field(default=12, ge=3, le=100)
    tessdata_path: Path | None = None
    api_keys: dict[str, PrincipalConfig] | str | None = None

    @field_validator("api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return json.loads(value)
        return value

    def resolved_api_keys(self) -> dict[str, PrincipalConfig]:
        if isinstance(self.api_keys, dict) and self.api_keys:
            return self.api_keys
        if self.env == "production" and self.auth_mode != "oidc":
            raise RuntimeError("EVIDENCEDESK_API_KEYS must be configured in production")
        if self.auth_mode == "oidc":
            return {}
        return {
            "dev-admin-key": PrincipalConfig(
                subject="local-admin",
                tenant_id="demo",
                role="admin",
                clearances=["public", "internal", "confidential"],
            ),
            "dev-reader-key": PrincipalConfig(
                subject="local-reader",
                tenant_id="demo",
                role="reader",
                clearances=["public", "internal"],
            ),
        }

    def validate_auth(self) -> None:
        if self.auth_mode in {"oidc", "hybrid"}:
            missing = [
                name
                for name, value in {
                    "EVIDENCEDESK_OIDC_ISSUER": self.oidc_issuer,
                    "EVIDENCEDESK_OIDC_AUDIENCE": self.oidc_audience,
                    "EVIDENCEDESK_OIDC_JWKS_URL": self.oidc_jwks_url,
                }.items()
                if not value
            ]
            if missing:
                raise RuntimeError(f"OIDC configuration is incomplete: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
