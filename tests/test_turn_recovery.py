"""Тесты фиксации прерванного черновика."""

from __future__ import annotations

import uuid

import pytest

from app.api.ws_manager import manager
from app.db.models import MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository
from app.db import session as db_session
from app.db.session import dispose_database, init_db
from tests.safety import assert_not_using_production_database, safe_configure_database
from app.services.turn_recovery import settle_interrupted_turn


@pytest.mark.asyncio
async def test_settle_keeps_partial_draft(tmp_path, repo_conv_title: str) -> None:
    await dispose_database()
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'recovery.sqlite'}"
    safe_configure_database(db_url)
    await init_db()
    assert_not_using_production_database()

    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(
            title=repo_conv_title,
            preset_id=preset.id,
        )
        draft = await MessageRepository(session).create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content_text="Частичный ответ",
            content_json={
                "streaming": True,
                "phase": "text",
                "images": ["/media/asset/11111111-1111-1111-1111-111111111111"],
            },
        )
        await session.commit()
        conv_id = conv.id
        draft_id = draft.id

    manager.set_streaming_message(conv_id, draft_id)

    async with db_session.async_session_factory() as session:
        kept = await settle_interrupted_turn(
            session,
            conv_id,
            status_code="llm_error",
            status_message="Сбой LLM",
        )
        await session.commit()
        assert kept is True

        msg = await MessageRepository(session).get_by_id(draft_id)
        assert msg is not None
        assert msg.content_text and "Частичный" in msg.content_text
        cj = msg.content_json or {}
        assert cj.get("streaming") is None
        assert cj.get("turn_status") == "llm_error"

    assert manager.get_streaming_message(conv_id) is None


@pytest.mark.asyncio
async def test_settle_deletes_empty_draft(tmp_path, repo_conv_title: str) -> None:
    await dispose_database()
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'empty.sqlite'}"
    safe_configure_database(db_url)
    await init_db()
    assert_not_using_production_database()

    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        conv = await ConversationRepository(session).create(
            title=repo_conv_title,
            preset_id=preset.id,
        )
        draft = await MessageRepository(session).create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content_text="",
            content_json={"streaming": True, "phase": "tool"},
        )
        await session.commit()
        conv_id = conv.id
        draft_id = draft.id

    async with db_session.async_session_factory() as session:
        kept = await settle_interrupted_turn(session, conv_id, status_code="cancelled")
        await session.commit()
        assert kept is False
        assert await MessageRepository(session).get_by_id(draft_id) is None
