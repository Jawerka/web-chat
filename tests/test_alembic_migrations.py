"""P2.1: Alembic upgrade на пустой SQLite."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.alembic_runner import run_alembic_upgrade
from app.db.session import configure_database, dispose_database, init_db
from tests.safety import assert_not_using_production_database, safe_configure_database


@pytest.mark.asyncio
async def test_alembic_upgrade_creates_tables(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'alembic_only.sqlite'}"
    safe_configure_database(db_url)
    assert_not_using_production_database()

    await dispose_database()
    configure_database(db_url)
    run_alembic_upgrade("head", database_url=db_url)

    from app.db import session as db_session

    async with db_session.async_session_factory() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"),
        )
        tables = {row[0] for row in result.fetchall()}
    assert "conversations" in tables
    assert "messages" in tables
    assert "media_assets" in tables
    await dispose_database()


@pytest.mark.asyncio
async def test_init_db_sqlite_still_uses_create_all(tmp_path) -> None:
    """LAN SQLite: create_all + migrate.py без обязательного alembic stamp."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'init.sqlite'}"
    await dispose_database()
    safe_configure_database(db_url)
    configure_database(db_url)
    await init_db()
    from app.db import session as db_session

    async with db_session.async_session_factory() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM presets"))
        assert (result.scalar() or 0) >= 1
    await dispose_database()
