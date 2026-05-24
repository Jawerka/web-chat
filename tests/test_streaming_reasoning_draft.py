"""reasoning_delta в AssistantStreamDraft."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.streaming_draft import AssistantStreamDraft


@pytest.mark.asyncio
async def test_on_reasoning_delta_emits_ws() -> None:
    session = AsyncMock()
    session.commit = AsyncMock()
    msg_repo = AsyncMock()
    conv_repo = AsyncMock()
    conversation = MagicMock()
    conversation.id = uuid.uuid4()
    message = MagicMock()
    message.id = uuid.uuid4()
    msg_repo.settle_stale_streaming_assistant_messages = AsyncMock(return_value=0)
    msg_repo.create = AsyncMock(return_value=message)
    msg_repo.update_content = AsyncMock()
    conv_repo.touch = AsyncMock()

    emit = AsyncMock()
    draft = AssistantStreamDraft(session, msg_repo, conv_repo, conversation, emit)

    await draft.on_reasoning_delta("step one")
    assert draft.reasoning == "step one"
    emit.assert_awaited()
    assert emit.await_args_list[-1][0][0] == "reasoning_delta"
    assert emit.await_args_list[-1][0][1]["content"] == "step one"
