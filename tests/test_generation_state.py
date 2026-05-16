"""Тесты состояния генерации и черновика при tool round."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.api.ws_manager import manager
from app.db.models import MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository
from app.db.session import async_session_factory, configure_database, init_db
from app.services.generation_state import get_generation_state
from app.services.streaming_draft import AssistantStreamDraft


@pytest.mark.asyncio
async def test_generation_status_includes_phase_from_draft(tmp_path) -> None:
    """get_generation_state возвращает phase/active_tool из content_json черновика."""
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
