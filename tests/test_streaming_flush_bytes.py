"""P1.1: сброс буфера стрима при достижении stream_flush_min_bytes."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.streaming_draft import AssistantStreamDraft


@pytest.mark.asyncio
async def test_large_delta_triggers_immediate_flush() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    msg_repo = AsyncMock()
    conv_repo = AsyncMock()
    conversation_id = uuid.uuid4()
    message = MagicMock()
    message.id = uuid.uuid4()
    message.content_text = ""
    message.content_json = {}
    msg_repo.settle_stale_streaming_assistant_messages = AsyncMock(return_value=0)
    msg_repo.create = AsyncMock(return_value=message)
    msg_repo.update_content = AsyncMock()
    msg_repo.get_by_id = AsyncMock(return_value=message)
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
        draft._draft_id = message.id
        with patch("app.services.streaming_draft.settings") as mock_settings:
            mock_settings.stream_flush_min_bytes = 100
            chunk = "x" * 150
            await draft.on_delta(chunk)
            await asyncio.sleep(0.05)

    assert msg_repo.update_content.await_count >= 1
