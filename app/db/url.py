"""
URL и тип backend БД (SQLite LAN по умолчанию, Postgres — P2.1).
"""

from __future__ import annotations

from app.config import settings


def database_url_raw(url: str | None = None) -> str:
    """Активный async URL (из settings или переопределение)."""
    return (url or settings.database_url).strip()


def is_sqlite_url(url: str | None = None) -> bool:
    return "sqlite" in database_url_raw(url).lower()


def is_postgres_url(url: str | None = None) -> bool:
    raw = database_url_raw(url).lower()
    return raw.startswith("postgresql") or raw.startswith("postgres+")


def alembic_database_url(url: str | None = None) -> str:
    """
    Sync URL для Alembic CLI (psycopg / sqlite3).

    ``postgresql+asyncpg://`` → ``postgresql+psycopg://``
  ``sqlite+aiosqlite://`` → ``sqlite://``
    """
    raw = database_url_raw(url)
    if "+asyncpg" in raw:
        return raw.replace("postgresql+asyncpg", "postgresql+psycopg", 1)
    if "+aiosqlite" in raw:
        return raw.replace("sqlite+aiosqlite", "sqlite", 1)
    return raw


def normalize_async_database_url(url: str | None = None) -> str:
    """Привести URL к async-драйверу для create_async_engine."""
    raw = database_url_raw(url)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql+psycopg://"):
        return raw.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    return raw
