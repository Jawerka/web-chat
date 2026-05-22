"""При лимите tools черновик финализируется, а не дублируется."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.db.models import Message, MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository
from app.db.session import async_session_factory, configure_database, dispose_database, init_db
from app.services.agent_orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_tool_limit_updates_existing_draft(tmp_path) -> None:
    await dispose_database()
    configure_database(f"sqlite+aiosqlite:///{tmp_path / 'limit.sqlite'}")
    await init_db()

    async with async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(title="t", preset_id=preset.id)
        msg_repo = MessageRepository(session)
        user = await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content_text="hi",
        )
        draft = await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content_text="",
            content_json={
                "streaming": True,
                "phase": "tool",
                "images": ["/media/asset/11111111-1111-1111-1111-111111111111"],
                "image_asset_ids": ["11111111-1111-1111-1111-111111111111"],
            },
        )
        await session.commit()
        conv_id = conv.id
        draft_id = draft.id
        user_id = user.id

    orchestrator = AgentOrchestrator()
    emit = AsyncMock()

    async with async_session_factory() as session:
        conv = await ConversationRepository(session).get_by_id(conv_id)
        assert conv is not None
        user = await MessageRepository(session).get_by_id(user_id)
        draft = await MessageRepository(session).get_by_id(draft_id)
        assert user is not None and draft is not None

        result = await orchestrator._complete_after_tool_limit(
            session,
            msg_repo=MessageRepository(session),
            conv_repo=ConversationRepository(session),
            conversation=conv,
            user_message=user,
            content_from_llm=None,
            all_image_urls=["/media/asset/11111111-1111-1111-1111-111111111111"],
            all_image_asset_ids=["11111111-1111-1111-1111-111111111111"],
            media_url_rewrites={},
            tool_calls_meta=[],
            emit=emit,
            existing_message=draft,
        )
        await session.commit()
        assert result is not None

        messages = await MessageRepository(session).list_for_conversation(conv_id, limit=50)
        assistants = [m for m in messages if m.role == MessageRole.ASSISTANT]
        assert len(assistants) == 1
        final = assistants[0]
        assert final.id == draft_id
        assert final.content_json.get("streaming") is None
        assert "лимит шагов" in (final.content_text or "").lower()
