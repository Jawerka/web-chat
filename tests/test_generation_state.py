"""Тесты состояния генерации и черновика при tool round."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.api.ws_manager import manager
from app.db.models import MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository
from app.db.session import async_session_factory, configure_database, dispose_database, init_db
from app.services.generation_state import get_generation_state
from app.services.streaming_draft import AssistantStreamDraft


@pytest.mark.asyncio
async def test_generation_status_includes_phase_from_draft(tmp_path) -> None:
    """get_generation_state возвращает phase/active_tool из content_json черновика."""
    await dispose_database()
    configure_database(f"sqlite+aiosqlite:///{tmp_path / 'phase.sqlite'}")
    await init_db()

    async with async_session_factory() as session:
        preset_repo = PresetRepository(session)
        preset = await preset_repo.get_default()
        assert preset is not None
        conv_repo = ConversationRepository(session)
        conversation = await conv_repo.create(title="t", preset_id=preset.id)
        msg_repo = MessageRepository(session)
        draft = await msg_repo.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content_text="",
            content_json={
                "streaming": True,
                "phase": "tool",
                "active_tool": "generate_image",
                "images": [],
            },
        )
        await session.commit()
        conv_id = conversation.id
        draft_id = draft.id

    manager.clear_streaming_message(conv_id)
    with patch.object(manager, "is_busy", return_value=True):
        async with async_session_factory() as session:
            state = await get_generation_state(session, conv_id)

    assert state["in_progress"] is True
    assert state["phase"] == "tool"
    assert state["active_tool"] == "generate_image"
    assert state["streaming_message_id"] == str(draft_id)


@pytest.mark.asyncio
async def test_stream_draft_enter_tool_round_keeps_message(tmp_path) -> None:
    """enter_tool_round не удаляет черновик, add_images сохраняет URL."""
    await dispose_database()
    configure_database(f"sqlite+aiosqlite:///{tmp_path / 'draft.sqlite'}")
    await init_db()

    emitted: list[tuple[str, dict]] = []

    async def emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    async with async_session_factory() as session:
        preset_repo = PresetRepository(session)
        preset = await preset_repo.get_default()
        assert preset is not None
        conv_repo = ConversationRepository(session)
        conversation = await conv_repo.create(title="t", preset_id=preset.id)
        msg_repo = MessageRepository(session)

        draft = AssistantStreamDraft(
            session,
            msg_repo,
            conv_repo,
            conversation,
            emit,
        )
        await draft.on_delta("Привет")
        await draft.enter_tool_round(active_tool="generate_image")
        await draft.add_images(
            ["/media/asset/550e8400-e29b-41d4-a716-446655440000"],
            ["550e8400-e29b-41d4-a716-446655440000"],
        )
        await session.commit()

        assert draft.message is not None
        cj = draft.message.content_json or {}
        assert cj.get("phase") == "tool"
        assert cj.get("active_tool") == "generate_image"
        assert "/media/asset/" in (cj.get("images") or [""])[0]


@pytest.mark.asyncio
async def test_settle_clears_streaming_when_last_is_user(tmp_path) -> None:
    """streaming:true снимается, если последнее сообщение — user."""
    await dispose_database()
    configure_database(f"sqlite+aiosqlite:///{tmp_path / 'stale.sqlite'}")
    await init_db()

    async with async_session_factory() as session:
        preset_repo = PresetRepository(session)
        preset = await preset_repo.get_default()
        assert preset is not None
        conv_repo = ConversationRepository(session)
        conversation = await conv_repo.create(title="t", preset_id=preset.id)
        msg_repo = MessageRepository(session)
        old = await msg_repo.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content_text="старое",
            content_json={"streaming": True, "images": ["/old"]},
        )
        await msg_repo.create(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content_text="новый вопрос",
        )
        await session.commit()
        conv_id = conversation.id
        old_id = old.id

    async with async_session_factory() as session:
        msg_repo = MessageRepository(session)
        settled = await msg_repo.settle_stale_streaming_assistant_messages(conv_id)
        await session.commit()
        assert settled == 1
        refreshed = await msg_repo.get_by_id(old_id)
        assert refreshed is not None
        assert refreshed.content_json.get("streaming") is False


@pytest.mark.asyncio
async def test_settle_keeps_streaming_only_on_last_assistant(tmp_path) -> None:
    """streaming:true остаётся только у последнего assistant в беседе."""
    await dispose_database()
    configure_database(f"sqlite+aiosqlite:///{tmp_path / 'two_drafts.sqlite'}")
    await init_db()

    async with async_session_factory() as session:
        preset_repo = PresetRepository(session)
        preset = await preset_repo.get_default()
        assert preset is not None
        conv_repo = ConversationRepository(session)
        conversation = await conv_repo.create(title="t", preset_id=preset.id)
        msg_repo = MessageRepository(session)
        first = await msg_repo.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content_text="первый",
            content_json={"streaming": True},
        )
        second = await msg_repo.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content_text="второй",
            content_json={"streaming": True},
        )
        await session.commit()
        conv_id = conversation.id
        first_id = first.id
        second_id = second.id

    async with async_session_factory() as session:
        msg_repo = MessageRepository(session)
        settled = await msg_repo.settle_stale_streaming_assistant_messages(conv_id)
        await session.commit()
        assert settled == 1
        a = await msg_repo.get_by_id(first_id)
        b = await msg_repo.get_by_id(second_id)
        assert a is not None and b is not None
        assert a.content_json.get("streaming") is False
        assert b.content_json.get("streaming") is True
        streaming = await msg_repo.get_streaming_assistant_message(conv_id)
        assert streaming is not None
        assert streaming.id == second_id
