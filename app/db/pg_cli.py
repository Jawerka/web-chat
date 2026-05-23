"""
Параметры подключения PostgreSQL для shell-утилит (pg_dump, pg_restore, psql).
"""

from __future__ import annotations

import shlex

from sqlalchemy.engine import make_url

from app.db.url import alembic_database_url, database_url_raw, is_postgres_url


class PgCliError(ValueError):
    """Некорректный или не-Postgres URL."""


def pg_connection_params(url: str | None = None) -> dict[str, str | int]:
    """Разбор DATABASE_URL в параметры для libpq / pg_dump."""
    raw = database_url_raw(url)
    if not is_postgres_url(raw):
        raise PgCliError(f"Ожидался PostgreSQL URL, получено: {raw!r}")
    sync_url = alembic_database_url(raw)
    parsed = make_url(sync_url)
    return {
        "host": parsed.host or "127.0.0.1",
        "port": int(parsed.port or 5432),
        "username": parsed.username or "postgres",
        "password": parsed.password or "",
        "database": parsed.database or "",
    }


def shell_exports(url: str | None = None) -> str:
    """Строки ``export PG*`` для bash (eval в скриптах бэкапа)."""
    p = pg_connection_params(url)
    parts = [
        f"export PGHOST={shlex.quote(str(p['host']))}",
        f"export PGPORT={shlex.quote(str(p['port']))}",
        f"export PGUSER={shlex.quote(str(p['username']))}",
        f"export PGDATABASE={shlex.quote(str(p['database']))}",
        f"export PGPASSWORD={shlex.quote(str(p['password']))}",
    ]
    return "\n".join(parts)
