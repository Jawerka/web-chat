"""Параллельные tool_calls в раунде (P4.2)."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.tool_executor import ToolResult
from app.services.agent_orchestrator import AgentOrchestrator, _SD_TOOL_SEMAPHORE
from app.services.turn_context import TurnContext


@pytest.mark.asyncio
async def test_sd_tools_serialized_in_one_round() -> None:
    """Два generate_image в одном раунде не пересекаются по времени."""
    sd_active = 0
    max_sd_active = 0
    track = asyncio.Lock()

    async def run_tool(name: str, _args: dict) -> ToolResult:
        nonlocal sd_active, max_sd_active
        if name != "generate_image":
            return ToolResult(content="ok", image_urls=[])
        async with track:
            sd_active += 1
            max_sd_active = max(max_sd_active, sd_active)
        await asyncio.sleep(0.06)
        async with track:
            sd_active -= 1
        return ToolResult(content="ok", image_urls=[])

    executor = MagicMock()
    executor.run = AsyncMock(side_effect=run_tool)

    ctx = TurnContext(
        conversation_id=uuid.uuid4(),
        user_message_id=uuid.uuid4(),
        emit=AsyncMock(),
        cancel_event=asyncio.Event(),
        llm_model=None,
        llm_messages=[],
    )
    ctx.stream_draft.set_active_tool = AsyncMock()
    ctx.stream_draft.add_images = AsyncMock()

    completion = MagicMock()
    completion.tool_calls = [
        {
            "id": "c1",
            "function": {"name": "generate_image", "arguments": '{"prompt": "a"}'},
        },
        {
            "id": "c2",
            "function": {"name": "generate_image", "arguments": '{"prompt": "b"}'},
        },
    ]

    orch = AgentOrchestrator()
    orch._llm = MagicMock()
    orch._llm.parse_tool_arguments = MagicMock(
        side_effect=[{"prompt": "a"}, {"prompt": "b"}],
    )

    await orch._run_completion_tool_calls(
        ctx=ctx,
        completion=completion,
        turn_executor=executor,
        round_idx=0,
    )

    assert executor.run.await_count == 2
    assert max_sd_active == 1


@pytest.mark.asyncio
async def test_extract_text_runs_in_parallel() -> None:
    """Два extract_text в раунде выполняются параллельно (быстрее последовательного)."""
    delay = 0.08

    async def run_tool(name: str, _args: dict) -> ToolResult:
        if name != "extract_text":
            return ToolResult(content="ok", image_urls=[])
        await asyncio.sleep(delay)
        return ToolResult(content="text", image_urls=[])

    executor = MagicMock()
    executor.run = AsyncMock(side_effect=run_tool)

    ctx = TurnContext(
        conversation_id=uuid.uuid4(),
        user_message_id=uuid.uuid4(),
        emit=AsyncMock(),
        cancel_event=asyncio.Event(),
        llm_model=None,
        llm_messages=[],
    )
    ctx.stream_draft.set_active_tool = AsyncMock()
    ctx.stream_draft.add_images = AsyncMock()

    completion = MagicMock()
    completion.tool_calls = [
        {
            "id": "e1",
            "function": {
                "name": "extract_text",
                "arguments": '{"attachment_id": "a"}',
            },
        },
        {
            "id": "e2",
            "function": {
                "name": "extract_text",
                "arguments": '{"attachment_id": "b"}',
            },
        },
    ]

    orch = AgentOrchestrator()
    orch._llm = MagicMock()
    orch._llm.parse_tool_arguments = MagicMock(
        side_effect=[{"attachment_id": "a"}, {"attachment_id": "b"}],
    )

    t0 = asyncio.get_event_loop().time()
    await orch._run_completion_tool_calls(
        ctx=ctx,
        completion=completion,
        turn_executor=executor,
        round_idx=0,
    )
    elapsed = asyncio.get_event_loop().time() - t0

    assert executor.run.await_count == 2
    assert elapsed < delay * 1.75


def test_sd_semaphore_is_binary() -> None:
    assert isinstance(_SD_TOOL_SEMAPHORE, asyncio.Semaphore)
