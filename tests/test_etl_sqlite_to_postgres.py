"""ETL SQLite → Postgres (и SQLite→SQLite в тестах)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.etl_sqlite_to_postgres import (
    EtlError,
    EtlOptions,
    count_all_tables,
    run_etl,
    validate_etl_urls,
)
from app.db.models import Conversation, Message, MessageRole, Preset
from app.db.session import configure_database, dispose_database, init_db
from app.db.repositories import (
    ConversationRepository,
    MessageRepository,
    PresetRepository,
)
from tests.safety import assert_not_using_production_database, safe_configure_database


def test_validate_etl_urls() -> None:
    src = "sqlite+aiosqlite:///./a.sqlite"
    dst = "postgresql+asyncpg://u:p@localhost/db"
    validate_etl_urls(src, dst)
    with pytest.raises(EtlError):
        validate_etl_urls(dst, src)


@pytest.mark.asyncio
async def test_etl_sqlite_to_sqlite_copy(tmp_path) -> None:
    """Полный цикл копирования на двух файлах SQLite (без Postgres)."""
    src_url = f"sqlite+aiosqlite:///{tmp_path / 'src.sqlite'}"
    dst_url = f"sqlite+aiosqlite:///{tmp_path / 'dst.sqlite'}"

    await dispose_database()
    safe_configure_database(src_url)
    configure_database(src_url)
    await init_db()

    from app.db import session as db_session

    async with db_session.async_session_factory() as session:
        preset_repo = PresetRepository(session)
        preset = await preset_repo.get_default()
        assert preset is not None
        conv_repo = ConversationRepository(session)
        conv = await conv_repo.create(title="ETL test", preset_id=preset.id)
        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content_text="hello etl",
        )
        await session.commit()

    await dispose_database()

    stats = await run_etl(
        EtlOptions(
            source_url=src_url,
            target_url=dst_url,
            truncate_target=True,
            stamp_alembic=False,
            batch_size=50,
        ),
    )
    assert stats.source["conversations"] >= 1
    assert stats.target_after["conversations"] == stats.source["conversations"]
    assert stats.target_after["messages"] == stats.source["messages"]

    configure_database(dst_url)
    async with db_session.async_session_factory() as session:
        result = await session.execute(
            select(Message).where(Message.content_text == "hello etl"),
        )
        assert result.scalar_one_or_none() is not None

    await dispose_database()


@pytest.mark.asyncio
async def test_etl_dry_run(tmp_path) -> None:
    src_url = f"sqlite+aiosqlite:///{tmp_path / 'dry_src.sqlite'}"
    dst_url = f"sqlite+aiosqlite:///{tmp_path / 'dry_dst.sqlite'}"

    await dispose_database()
    safe_configure_database(src_url)
    configure_database(src_url)
    await init_db()
    await dispose_database()

    stats = await run_etl(
        EtlOptions(
            source_url=src_url,
            target_url=dst_url,
            dry_run=True,
        ),
    )
    assert stats.source["presets"] >= 1
    assert sum(stats.copied.values()) == 0
