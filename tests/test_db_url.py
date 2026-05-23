"""P2.1: URL БД и Alembic."""

from __future__ import annotations

from app.db.url import (
    alembic_database_url,
    is_postgres_url,
    is_sqlite_url,
    normalize_async_database_url,
)


def test_normalize_postgres_async() -> None:
    url = "postgresql://user:pass@db:5432/web_chat"
    assert normalize_async_database_url(url) == "postgresql+asyncpg://user:pass@db:5432/web_chat"


def test_alembic_sync_psycopg() -> None:
    url = "postgresql+asyncpg://user:pass@db:5432/web_chat"
    assert alembic_database_url(url) == "postgresql+psycopg://user:pass@db:5432/web_chat"


def test_alembic_sync_sqlite() -> None:
    url = "sqlite+aiosqlite:///./data/db/x.sqlite"
    assert alembic_database_url(url) == "sqlite:///./data/db/x.sqlite"


def test_backend_detection() -> None:
    assert is_sqlite_url("sqlite+aiosqlite:///x.sqlite")
    assert not is_postgres_url("sqlite+aiosqlite:///x.sqlite")
    assert is_postgres_url("postgresql+asyncpg://localhost/db")
