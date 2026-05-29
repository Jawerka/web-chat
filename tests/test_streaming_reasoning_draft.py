"""reasoning_delta в AssistantStreamDraft."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.streaming_draft import AssistantStreamDraft


@pytest.mark.asyncio
async def test_on_reasoning_delta_emits_ws() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    msg_repo = AsyncMock()
    conv_repo = AsyncMock()
    conversation_id = uuid.uuid4()
    message = MagicMock()
    message.id = uuid.uuid4()
    msg_repo.settle_stale_streaming_assistant_messages = AsyncMock(return_value=0)
    msg_repo.create = AsyncMock(return_value=message)
    msg_repo.update_content = AsyncMock()
    conv_repo.touch = AsyncMock()
    conv_repo.get_by_id = AsyncMock(return_value=MagicMock())

    emit = AsyncMock()

    @asynccontextmanager
    async def fake_open_turn_session():
        yield session

    with (
        patch("app.services.streaming_draft.open_turn_session", fake_open_turn_session),
        patch("app.services.streaming_draft.MessageRepository", lambda _s: msg_repo),
        patch("app.services.streaming_draft.ConversationRepository", lambda _s: conv_repo),
    ):
        draft = AssistantStreamDraft(conversation_id, emit)
        await draft.on_reasoning_delta("step one")

    assert draft.reasoning == "step one"
    emit.assert_awaited()
    assert emit.await_args_list[-1][0][0] == "reasoning_delta"
    assert emit.await_args_list[-1][0][1]["content"] == "step one"
