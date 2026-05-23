"""P1.1: сброс буфера стрима при достижении stream_flush_min_bytes."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.streaming_draft import AssistantStreamDraft


@pytest.mark.asyncio
async def test_large_delta_triggers_immediate_flush() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    msg_repo = AsyncMock()
    conv_repo = AsyncMock()
    conversation = MagicMock()
    conversation.id = uuid.uuid4()
    message = MagicMock()
    message.id = uuid.uuid4()
    message.content_text = ""
    message.content_json = {}
    msg_repo.settle_stale_streaming_assistant_messages = AsyncMock(return_value=0)
    msg_repo.create = AsyncMock(return_value=message)
    msg_repo.update_content = AsyncMock()
    conv_repo.touch = AsyncMock()

    emit = AsyncMock()
    draft = AssistantStreamDraft(session, msg_repo, conv_repo, conversation, emit)

    with patch("app.services.streaming_draft.settings") as mock_settings:
        mock_settings.stream_flush_min_bytes = 100
        chunk = "x" * 150
        await draft.on_delta(chunk)
        await asyncio.sleep(0.05)

    assert msg_repo.update_content.await_count >= 1
