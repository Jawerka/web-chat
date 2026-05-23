"""Параметры pg_dump из DATABASE_URL."""

from __future__ import annotations

import pytest

from app.db.pg_cli import PgCliError, pg_connection_params, shell_exports


def test_pg_connection_params() -> None:
    p = pg_connection_params("postgresql+asyncpg://u:secret@db.example:5433/mydb")
    assert p["host"] == "db.example"
    assert p["port"] == 5433
    assert p["username"] == "u"
    assert p["password"] == "secret"
    assert p["database"] == "mydb"


def test_pg_connection_params_rejects_sqlite() -> None:
    with pytest.raises(PgCliError):
        pg_connection_params("sqlite+aiosqlite:///x.sqlite")


def test_shell_exports_contains_password() -> None:
    out = shell_exports("postgresql+asyncpg://u:pw@127.0.0.1:5432/web_chat")
    assert "PGPASSWORD=" in out
    assert "PGDATABASE=" in out
