"""P1.1: параллельные записи через run_write не падают с database is locked."""

from __future__ import annotations

import asyncio

import pytest

from app.db import session as db_session
from app.db.models import MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository
from app.db.session import dispose_database, init_db
from app.db.sqlite import run_write
from tests.safety import assert_not_using_production_database, safe_configure_database


@pytest.mark.asyncio
async def test_parallel_run_write_messages(tmp_path, repo_conv_title: str) -> None:
    await dispose_database()
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'concurrent.sqlite'}"
    safe_configure_database(db_url)
    await init_db()
    assert_not_using_production_database()

    factory = db_session.async_session_factory

    async def setup(session):
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(
            title=repo_conv_title,
            preset_id=preset.id,
        )
        return conv.id

    conv_id = await run_write(factory, setup, operation="setup_conv")

    async def add_message(index: int) -> None:
        async def cb(session):
            await MessageRepository(session).create(
                conversation_id=conv_id,
                role=MessageRole.USER,
                content_text=f"parallel-{index}",
            )

        await run_write(factory, cb, operation=f"msg-{index}")

    await asyncio.gather(*[add_message(i) for i in range(12)])

    async with factory() as session:
        msgs = await MessageRepository(session).list_for_conversation(conv_id)
    assert len(msgs) == 12
    bodies = {m.content_text for m in msgs}
    assert bodies == {f"parallel-{i}" for i in range(12)}
