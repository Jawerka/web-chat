"""Тесты финальной очистки бесед с префиксом [pytest]."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.db import session as db_session
from app.db.repositories import ConversationRepository, PresetRepository
from app.db.session import dispose_database, init_db
from tests.safety import safe_configure_database
from tests.cleanup import (
    clear_recorded_conversation_ids,
    delete_test_conversations_in_session,
    record_test_conversation_id,
    should_cleanup_orphan_titles_on_live,
)
from tests.conventions import is_likely_test_orphan_title, is_test_conversation_title
from tests.conventions import TEST_CONVERSATION_PREFIX, format_test_conversation_title


@pytest.mark.asyncio
async def test_is_test_conversation_title() -> None:
    assert is_test_conversation_title("[pytest] foo")
    assert not is_test_conversation_title("Новая беседа")
    assert not is_test_conversation_title("[pyteste] wrong")


@pytest.mark.asyncio
async def test_is_likely_test_orphan_title() -> None:
    assert is_likely_test_orphan_title("t")
    assert is_likely_test_orphan_title("Новая беседа")
    assert is_likely_test_orphan_title("Переименована")
    assert not is_likely_test_orphan_title("[pytest] ok")
    assert not is_likely_test_orphan_title("Мой рабочий чат")


def test_orphan_live_cleanup_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_CHAT_TEST_CLEANUP_ORPHANS", raising=False)
    monkeypatch.delenv("WEB_CHAT_TEST_BASE_URL", raising=False)
    assert not should_cleanup_orphan_titles_on_live()


@pytest.mark.asyncio
async def test_format_test_conversation_title_unique() -> None:
    a = format_test_conversation_title("tests/test_x.py::test_a")
    b = format_test_conversation_title("tests/test_x.py::test_a")
    assert a.startswith(TEST_CONVERSATION_PREFIX)
    assert a != b


@pytest.mark.asyncio
async def test_delete_test_conversations_in_session(tmp_path) -> None:
    await dispose_database()
    db_file = tmp_path / "cleanup.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    safe_configure_database(db_url)
    await init_db()

    try:
        async with db_session.async_session_factory() as session:
            preset = await PresetRepository(session).get_default()
            assert preset is not None
            repo = ConversationRepository(session)
            await repo.create(title="Новая беседа", preset_id=preset.id)
            await repo.create(
                title=f"{TEST_CONVERSATION_PREFIX} оставить для удаления",
                preset_id=preset.id,
            )
            await repo.create(
                title=f"{TEST_CONVERSATION_PREFIX} вторая",
                preset_id=preset.id,
            )
            await session.commit()

            deleted = await delete_test_conversations_in_session(
                session,
                database_url=db_url,
            )
            assert deleted == 2

            session.expire_all()
            assert len(await repo.list_with_title_prefix(TEST_CONVERSATION_PREFIX)) == 0
            titles = {c.title for c in await repo.list_all()}
            assert "Новая беседа" in titles
    finally:
        await dispose_database()


@pytest.mark.asyncio
async def test_delete_registered_conversation_ids(tmp_path) -> None:
    """Беседы без префикса, но с зарегистрированным id, удаляются финишером."""
    clear_recorded_conversation_ids()
    await dispose_database()
    db_file = tmp_path / "registered.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    safe_configure_database(db_url)
    await init_db()

    try:
        async with db_session.async_session_factory() as session:
            preset = await PresetRepository(session).get_default()
            assert preset is not None
            repo = ConversationRepository(session)
            orphan = await repo.create(title="Новая беседа", preset_id=preset.id)
            await repo.create(
                title=f"{TEST_CONVERSATION_PREFIX} prefixed",
                preset_id=preset.id,
            )
            await session.commit()
            record_test_conversation_id(orphan.id, database_url=db_url)

            deleted = await delete_test_conversations_in_session(
                session,
                database_url=db_url,
            )
            assert deleted == 2

            session.expire_all()
            assert len(await repo.list_all()) == 0
    finally:
        await dispose_database()


@pytest.mark.asyncio
async def test_test_conversation_fixture_uses_prefix(
    client: AsyncClient,
    test_conversation: dict,
) -> None:
    assert test_conversation["title"].startswith(TEST_CONVERSATION_PREFIX)
    listed = await client.get("/api/conversations")
    assert any(c["id"] == test_conversation["id"] for c in listed.json())
