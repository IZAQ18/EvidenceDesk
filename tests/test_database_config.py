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
