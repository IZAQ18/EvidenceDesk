from __future__ import annotations

import pytest

from app.config import Settings
from app.db import PostgresConnection, PostgresDatabase, build_database


class FakeConnection:
    def __init__(self):
        self.query = ""
        self.parameters = ()

    def execute(self, query, parameters=None):
        self.query = query
        self.parameters = parameters
        return self


def test_postgres_placeholder_adapter_preserves_literal_percent_patterns():
    raw = FakeConnection()
    connection = PostgresConnection(raw)
    connection.execute(
        "SELECT * FROM documents WHERE tenant_id = ? AND extraction_method LIKE '%ocr%'",
        ("tenant-a",),
    )
    assert "tenant_id = %s" in raw.query
    assert "LIKE '%%ocr%%'" in raw.query
    assert raw.parameters == ("tenant-a",)


def test_postgres_configuration_fails_closed_without_url_or_tls(tmp_path):
    missing = Settings(
        _env_file=None,
        database_backend="postgres",
        db_path=tmp_path / "unused.db",
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_database(missing)

    insecure = Settings(
        _env_file=None,
        env="production",
        database_backend="postgres",
        database_url="postgresql://user:password@database.example/evidencedesk",
        db_path=tmp_path / "unused.db",
    )
    with pytest.raises(RuntimeError, match="require TLS"):
        build_database(insecure)

    secure = Settings(
        _env_file=None,
        env="production",
        database_backend="postgres",
        database_url=(
            "postgresql://user:password@database.example/evidencedesk?sslmode=verify-full"
        ),
        db_path=tmp_path / "unused.db",
    )
    database = build_database(secure)
    assert isinstance(database, PostgresDatabase)


def test_database_pool_size_is_bounded():
    with pytest.raises(ValueError, match="pool max size"):
        Settings(
            _env_file=None,
            database_pool_min_size=5,
            database_pool_max_size=2,
        )


def test_production_accounts_require_managed_storage_or_explicit_test_mode(tmp_path):
    unsafe = Settings(
        _env_file=None,
        env="production",
        accounts_enabled=True,
        db_path=tmp_path / "temporary.db",
        document_store_path=tmp_path / "originals",
    )
    with pytest.raises(RuntimeError, match="requires PostgreSQL and S3"):
        unsafe.validate_account_storage()

    acceptance = unsafe.model_copy(update={"ephemeral_account_testing_enabled": True})
    acceptance.validate_account_storage()
    assert acceptance.account_storage_is_durable() is False

    managed = Settings(
        _env_file=None,
        env="production",
        accounts_enabled=True,
        database_backend="postgres",
        database_url=(
            "postgresql://user:password@database.example/evidencedesk?sslmode=require"
        ),
        document_store_backend="s3",
        s3_bucket="evidencedesk-private",
    )
    managed.validate_account_storage()
    assert managed.account_storage_is_durable() is True


def test_public_customer_launch_requires_all_operational_controls():
    incomplete = Settings(
        _env_file=None,
        env="production",
        public_customer_launch_enabled=True,
        accounts_enabled=True,
        google_oauth_enabled=True,
        google_oauth_registration_enabled=True,
        google_oauth_client_id="client.apps.googleusercontent.com",
        google_oauth_client_secret="secret",
        public_base_url="https://evidencedesk.example",
        database_backend="postgres",
        database_url=(
            "postgresql://user:password@database.example/evidencedesk?sslmode=require"
        ),
        document_store_backend="s3",
        s3_bucket="evidencedesk-private",
    )
    with pytest.raises(RuntimeError, match="ClamAV scanning"):
        incomplete.validate_public_customer_launch()

    ready = incomplete.model_copy(
        update={
            "malware_scanner_backend": "clamd",
            "malware_scan_required": True,
            "account_export_enabled": True,
            "account_deletion_enabled": True,
            "edge_rate_limiting_confirmed": True,
            "monitoring_confirmed": True,
        }
    )
    ready.validate_public_customer_launch()


def test_public_launch_never_allows_ephemeral_account_override():
    settings = Settings(
        _env_file=None,
        env="production",
        public_customer_launch_enabled=True,
        accounts_enabled=True,
        ephemeral_account_testing_enabled=True,
        account_registration_enabled=True,
        database_backend="postgres",
        database_url=(
            "postgresql://user:password@database.example/evidencedesk?sslmode=require"
        ),
        document_store_backend="s3",
        s3_bucket="evidencedesk-private",
        malware_scanner_backend="clamd",
        malware_scan_required=True,
        account_export_enabled=True,
        account_deletion_enabled=True,
        edge_rate_limiting_confirmed=True,
        monitoring_confirmed=True,
    )
    with pytest.raises(RuntimeError, match="ephemeral account testing"):
        settings.validate_public_customer_launch()


def test_postgres_components_build_encoded_tls_url():
    settings = Settings(
        _env_file=None,
        env="production",
        database_backend="postgres",
        database_host="private-db.example.com",
        database_name="evidencedesk",
        database_user="application user",
        database_password="p@ss/word",
        database_sslmode="verify-full",
    )
    database = build_database(settings)
    assert isinstance(database, PostgresDatabase)
    assert database.url == (
        "postgresql://application%20user:p%40ss%2Fword@private-db.example.com:5432/"
        "evidencedesk?sslmode=verify-full"
    )


def test_postgres_components_reject_blank_password_and_unsafe_host():
    blank_password = Settings(
        _env_file=None,
        database_backend="postgres",
        database_host="private-db.example.com",
        database_name="evidencedesk",
        database_user="application",
        database_password="",
    )
    with pytest.raises(RuntimeError, match="DATABASE_PASSWORD"):
        build_database(blank_password)

    unsafe_host = Settings(
        _env_file=None,
        database_backend="postgres",
        database_host="host.example/x",
        database_name="evidencedesk",
        database_user="application",
        database_password="secret",
    )
    with pytest.raises(RuntimeError, match="invalid characters"):
        build_database(unsafe_host)
