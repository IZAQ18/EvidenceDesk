from __future__ import annotations

import json
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, quote, urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
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
    accounts_enabled: bool = False
    account_registration_enabled: bool = False
    account_session_hours: int = Field(default=24 * 7, ge=1, le=24 * 30)
    account_email_verification_required: bool = False
    account_password_reset_enabled: bool = False
    account_invitations_enabled: bool = False
    account_export_enabled: bool = False
    account_deletion_enabled: bool = False
    account_verification_token_hours: int = Field(default=24, ge=1, le=168)
    account_password_reset_token_minutes: int = Field(default=30, ge=10, le=120)
    account_invitation_hours: int = Field(default=72, ge=1, le=336)
    account_export_max_bytes: int = Field(default=250_000_000, ge=1_000_000, le=1_000_000_000)
    email_delivery_backend: Literal["disabled", "smtp"] = "disabled"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_address: str | None = None
    smtp_starttls: bool = True
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_tenant_claim: str = "tenant_id"
    oidc_role_claim: str = "role"
    oidc_clearances_claim: str = "clearances"
    database_backend: Literal["sqlite", "postgres"] = "sqlite"
    db_path: Path = Path("data/evidencedesk.db")
    database_url: SecretStr | None = None
    database_host: str | None = None
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
    database_user: str | None = None
    database_password: SecretStr | None = None
    database_sslmode: Literal["require", "verify-ca", "verify-full"] = "require"
    database_pool_min_size: int = Field(default=1, ge=1, le=20)
    database_pool_max_size: int = Field(default=5, ge=1, le=100)
    database_pool_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    document_store_path: Path = Path("data/originals")
    document_store_backend: Literal["local", "s3"] = "local"
    s3_bucket: str | None = Field(default=None, min_length=3, max_length=63)
    s3_prefix: str = Field(default="evidencedesk/originals", min_length=1, max_length=200)
    s3_region: str | None = Field(default=None, max_length=50)
    s3_endpoint_url: str | None = None
    s3_kms_key_id: SecretStr | None = None
    s3_expected_bucket_owner: str | None = Field(default=None, pattern=r"^\d{12}$")
    durability_manifest_key: SecretStr | None = Field(default=None, min_length=32, max_length=512)
    max_upload_bytes: int = Field(default=10_000_000, ge=1, le=50_000_000)
    ingestion_worker_enabled: bool = True
    ingestion_worker_poll_seconds: float = Field(default=0.5, ge=0.1, le=30)
    ingestion_job_lease_seconds: int = Field(default=300, ge=30, le=3_600)
    ingestion_job_max_attempts: int = Field(default=3, ge=1, le=10)
    malware_scanner_backend: Literal["disabled", "clamd"] = "disabled"
    malware_scan_required: bool = False
    malware_scan_timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    clamd_host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    clamd_port: int = Field(default=3310, ge=1, le=65535)
    ingestion_process_isolation_enabled: bool = False
    ingestion_extraction_timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    ingestion_worker_memory_mb: int = Field(default=1_024, ge=256, le=8_192)
    ingestion_worker_cpu_seconds: int = Field(default=90, ge=5, le=600)
    ingestion_worker_nice_level: int = Field(default=10, ge=0, le=19)
    ingestion_max_extracted_chars: int = Field(default=5_000_000, ge=10_000, le=20_000_000)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    max_chunks_per_document: int = Field(default=2_000, ge=1, le=20_000)
    semantic_candidate_limit: int = Field(default=600, ge=50, le=5_000)
    semantic_min_score: float = Field(default=0.08, ge=0.0, le=1.0)
    model_gateway_enabled: bool = False
    model_provider: Literal["openai_responses"] = "openai_responses"
    model_api_key: SecretStr | None = None
    model_base_url: str = "https://api.openai.com/v1"
    model_name: str = Field(default="gpt-5-mini", min_length=1, max_length=100)
    model_timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    model_max_evidence_chars: int = Field(default=12_000, ge=1_000, le=50_000)
    model_max_output_tokens: int = Field(default=600, ge=100, le=2_000)
    model_daily_request_limit: int = Field(default=100, ge=1, le=100_000)
    model_daily_token_limit: int = Field(default=250_000, ge=1_000, le=100_000_000)
    model_daily_cost_limit_cents: int = Field(default=100, ge=1, le=1_000_000)
    model_input_price_per_million_usd: Decimal = Field(default=Decimal("0"), ge=0)
    model_output_price_per_million_usd: Decimal = Field(default=Decimal("0"), ge=0)
    max_pdf_pages: int = Field(default=100, ge=1, le=500)
    ocr_enabled: bool = True
    ocr_languages: str = Field(default="eng", pattern=r"^[a-zA-Z0-9_+.-]+$")
    ocr_dpi: int = Field(default=200, ge=100, le=300)
    ocr_max_pages: int = Field(default=30, ge=1, le=100)
    ocr_min_words_per_page: int = Field(default=12, ge=3, le=100)
    tessdata_path: Path | None = None
    api_keys: dict[str, PrincipalConfig] | str | None = None
    pilot_access_code: str | None = Field(default=None, min_length=12, max_length=200)
    pilot_session_hours: int = Field(default=12, ge=1, le=168)
    pilot_tenant_id: str = Field(default="pilot", min_length=1, max_length=100)
    seed_demo_content: bool = False
    expose_api_docs: bool = False
    public_base_url: str | None = None
    support_url: str | None = None
    checkout_url: str | None = None
    pro_checkout_url: str | None = None
    payment_provider: Literal["lemonsqueezy", "fastspring"] = "lemonsqueezy"
    payment_webhook_secret: SecretStr | None = None
    payment_entitlements_enabled: bool = False
    payment_pro_variant_ids: str = Field(default="", max_length=1_000)
    fastspring_storefront_url: str | None = None
    fastspring_product_path: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$"
    )
    allowed_hosts: str = "localhost,127.0.0.1,testserver,*.onrender.com,*.koyeb.app"

    @model_validator(mode="after")
    def validate_pool_size(self) -> Settings:
        if self.database_pool_max_size < self.database_pool_min_size:
            raise ValueError("Database pool max size must be at least the min size")
        return self

    @field_validator("api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return json.loads(value)
        return value

    @field_validator("support_url", "checkout_url", "pro_checkout_url")
    @classmethod
    def validate_support_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if not normalized.startswith("https://"):
            raise ValueError("Payment and support URLs must use HTTPS")
        return normalized

    @field_validator("fastspring_storefront_url")
    @classmethod
    def validate_fastspring_storefront_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().rstrip("/")
        parts = urlsplit(normalized)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or not parts.hostname.endswith(".onfastspring.com")
            or not parts.path.startswith("/popup-")
        ):
            raise ValueError("FastSpring storefront must be an HTTPS popup checkout URL")
        return normalized

    def resolved_payment_pro_variant_ids(self) -> set[str]:
        values = {item.strip() for item in self.payment_pro_variant_ids.split(",") if item.strip()}
        if self.payment_provider == "lemonsqueezy":
            return {item for item in values if item.isdigit()}
        return {item for item in values if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item)}

    def constrained_ingestion_profile(self) -> bool:
        """Use a health-preserving OCR budget on small isolated workers."""
        return (
            self.ingestion_process_isolation_enabled
            and self.ingestion_worker_memory_mb <= 384
        )

    def effective_ocr_dpi(self) -> int:
        return min(self.ocr_dpi, 110) if self.constrained_ingestion_profile() else self.ocr_dpi

    def effective_ocr_max_pages(self) -> int:
        return (
            min(self.ocr_max_pages, 4)
            if self.constrained_ingestion_profile()
            else self.ocr_max_pages
        )

    def effective_extraction_timeout_seconds(self) -> float:
        return (
            min(self.ingestion_extraction_timeout_seconds, 40.0)
            if self.constrained_ingestion_profile()
            else self.ingestion_extraction_timeout_seconds
        )

    def effective_worker_cpu_seconds(self) -> int:
        return (
            min(self.ingestion_worker_cpu_seconds, 35)
            if self.constrained_ingestion_profile()
            else self.ingestion_worker_cpu_seconds
        )

    @model_validator(mode="after")
    def validate_payment_entitlements(self) -> Settings:
        if not self.payment_entitlements_enabled:
            return self
        if not self.accounts_enabled:
            raise ValueError("Payment entitlements require account authentication")
        if self.payment_webhook_secret is None:
            raise ValueError("Payment entitlements require a webhook secret")
        if self.payment_provider == "lemonsqueezy" and self.pro_checkout_url is None:
            raise ValueError("Lemon Squeezy entitlements require a Pro checkout URL")
        if self.payment_provider == "fastspring" and (
            self.fastspring_storefront_url is None or self.fastspring_product_path is None
        ):
            raise ValueError("FastSpring entitlements require a storefront URL and product path")
        configured_variants = [
            item.strip() for item in self.payment_pro_variant_ids.split(",") if item.strip()
        ]
        invalid_variant = (
            any(not item.isdigit() for item in configured_variants)
            if self.payment_provider == "lemonsqueezy"
            else any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item) for item in configured_variants)
        )
        if not configured_variants or invalid_variant:
            raise ValueError("Payment entitlements require approved Pro variant IDs")
        if (
            self.payment_provider == "fastspring"
            and self.fastspring_product_path not in configured_variants
        ):
            raise ValueError("FastSpring product path must be in approved Pro variant IDs")
        return self

    @field_validator("model_base_url")
    @classmethod
    def validate_model_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    def resolved_api_keys(self) -> dict[str, PrincipalConfig]:
        if isinstance(self.api_keys, dict) and self.api_keys:
            return self.api_keys
        if (
            self.env == "production"
            and self.auth_mode != "oidc"
            and not self.pilot_access_code
            and not self.accounts_enabled
        ):
            raise RuntimeError("EVIDENCEDESK_API_KEYS must be configured in production")
        if self.pilot_access_code:
            return {}
        if self.accounts_enabled:
            return {}
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
        if self.accounts_enabled and self.pilot_access_code:
            raise RuntimeError(
                "Account mode and the shared pilot access code are mutually exclusive"
            )
        if self.account_registration_enabled and not self.accounts_enabled:
            raise RuntimeError("Account registration requires EVIDENCEDESK_ACCOUNTS_ENABLED=true")
        if (
            self.account_export_enabled or self.account_deletion_enabled
        ) and not self.accounts_enabled:
            raise RuntimeError("Account controls require EVIDENCEDESK_ACCOUNTS_ENABLED=true")
        account_email_features = (
            self.account_email_verification_required
            or self.account_password_reset_enabled
            or self.account_invitations_enabled
        )
        if account_email_features and not self.accounts_enabled:
            raise RuntimeError("Account email features require EVIDENCEDESK_ACCOUNTS_ENABLED=true")
        if account_email_features and self.env == "production":
            if self.email_delivery_backend != "smtp":
                raise RuntimeError("Production account email features require SMTP delivery")
            missing_email = [
                name
                for name, value in {
                    "EVIDENCEDESK_SMTP_HOST": self.smtp_host,
                    "EVIDENCEDESK_SMTP_FROM_ADDRESS": self.smtp_from_address,
                    "EVIDENCEDESK_PUBLIC_BASE_URL": self.public_base_url,
                }.items()
                if not value or not value.strip()
            ]
            if missing_email:
                raise RuntimeError(
                    "Account email configuration is incomplete: " + ", ".join(missing_email)
                )
            if not self.public_base_url or not self.public_base_url.startswith("https://"):
                raise RuntimeError("Production account links require an HTTPS public base URL")
            if not self.smtp_starttls:
                raise RuntimeError("Production SMTP delivery must use STARTTLS")
        smtp_username = (self.smtp_username or "").strip()
        smtp_password = self.smtp_password.get_secret_value().strip() if self.smtp_password else ""
        if bool(smtp_username) != bool(smtp_password):
            raise RuntimeError("SMTP username and password must be configured together")
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

    def validate_storage(self) -> None:
        if self.document_store_backend != "s3":
            return
        if not self.s3_bucket:
            raise RuntimeError("EVIDENCEDESK_S3_BUCKET is required for S3 document storage")
        if self.env == "production" and self.s3_endpoint_url:
            if not self.s3_endpoint_url.startswith("https://"):
                raise RuntimeError("Production S3 endpoints must use HTTPS")

    def validate_ingestion_security(self) -> None:
        if self.malware_scan_required and self.malware_scanner_backend == "disabled":
            raise RuntimeError(
                "Required malware scanning needs EVIDENCEDESK_MALWARE_SCANNER_BACKEND=clamd"
            )
        if any(character in self.clamd_host for character in "/?#@ "):
            raise RuntimeError("ClamAV host contains invalid characters")

    def validate_database(self) -> None:
        if self.database_backend != "postgres":
            return
        if self.database_url is None:
            components: dict[str, str | None] = {
                "EVIDENCEDESK_DATABASE_HOST": self.database_host,
                "EVIDENCEDESK_DATABASE_NAME": self.database_name,
                "EVIDENCEDESK_DATABASE_USER": self.database_user,
                "EVIDENCEDESK_DATABASE_PASSWORD": (
                    self.database_password.get_secret_value()
                    if self.database_password is not None
                    else None
                ),
            }
            missing = [name for name, value in components.items() if not value or not value.strip()]
            if missing:
                raise RuntimeError(
                    "EVIDENCEDESK_DATABASE_URL or complete PostgreSQL components are required: "
                    + ", ".join(missing)
                )
            assert self.database_host
            if any(character in self.database_host for character in "/?#@ "):
                raise RuntimeError("PostgreSQL host contains invalid characters")
        if self.env == "production":
            query = parse_qs(urlsplit(self.resolved_database_url()).query)
            secure_modes = {"require", "verify-ca", "verify-full"}
            if not set(value.lower() for value in query.get("sslmode", [])) & secure_modes:
                raise RuntimeError("Production PostgreSQL connections must require TLS")

    def resolved_database_url(self) -> str:
        if self.database_url is not None:
            return self.database_url.get_secret_value()
        if not all(
            [
                self.database_host,
                self.database_name,
                self.database_user,
                self.database_password,
            ]
        ):
            raise RuntimeError("PostgreSQL connection configuration is incomplete")
        password = self.database_password.get_secret_value()
        return (
            f"postgresql://{quote(self.database_user or '', safe='')}:"
            f"{quote(password, safe='')}@{self.database_host}:{self.database_port}/"
            f"{quote(self.database_name or '', safe='')}?sslmode={self.database_sslmode}"
        )

    def validate_model_gateway(self) -> None:
        if not self.model_gateway_enabled:
            return
        if self.model_api_key is None or not self.model_api_key.get_secret_value().strip():
            raise RuntimeError(
                "EVIDENCEDESK_MODEL_API_KEY is required when the model gateway is enabled"
            )
        if not self.model_base_url.startswith("https://"):
            raise RuntimeError("The model gateway base URL must use HTTPS")
        if (
            self.model_input_price_per_million_usd <= 0
            or self.model_output_price_per_million_usd <= 0
        ):
            raise RuntimeError(
                "Configure current input and output token prices before enabling the model gateway"
            )

    def resolved_allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
