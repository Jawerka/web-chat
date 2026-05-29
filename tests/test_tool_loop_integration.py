"""Интеграционный тест: лимит img2img в одном ходе."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db import session as db_session
from app.integrations.llm_client import LLMCompletion
from app.services.agent_orchestrator import AgentOrchestrator
from tests.helpers import api_create_conversation


@pytest.mark.asyncio
async def test_fifth_img2img_in_turn_raises_tool_loop(
    client,
    test_conv_title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пять одинаковых img2img подряд → ToolLoopExceeded до MAX_TOOL_ROUNDS."""
    from app.config import settings

    monkeypatch.setattr(settings, "max_same_tool_per_turn", 3)

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "img2img",
            "arguments": '{"prompt": "loop"}',
        },
    }
    completion = LLMCompletion(
        content=None,
        tool_calls=[tool_call],
        finish_reason="tool_calls",
    )

    round_no = 0

    def _next_args(_raw: str) -> dict:
        nonlocal round_no
        round_no += 1
        return {"prompt": f"loop-{round_no}"}

    mock_llm = MagicMock()
    mock_llm.parse_tool_arguments = MagicMock(side_effect=_next_args)
    mock_llm.complete_with_stream = AsyncMock(return_value=completion)

    mock_tools = MagicMock()
    mock_tools.run = AsyncMock(
        return_value=MagicMock(content="ok", image_urls=[], image_asset_ids=[]),
    )

    orchestrator = AgentOrchestrator(llm=mock_llm, tool_executor=mock_tools)
    emit = AsyncMock()

    result = await orchestrator.run_conversation_turn(
        conv_id,
        "нарисуй",
        [],
        emit,
        asyncio.Event(),
    )

    assert mock_tools.run.await_count == 3
    assert result.image_urls == []
    assert any(c.args[0] == "done" for c in emit.call_args_list)
