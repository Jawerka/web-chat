"""Тесты anti-loop и раннего завершения хода."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.seed import IMAGE_GEN_PROMPT
from app.integrations.llm_client import LLMCompletion
from app.services.agent_orchestrator import AgentOrchestrator
from app.services.turn_context import TurnContext


@pytest.mark.asyncio
async def test_early_done_after_consecutive_duplicate_skips(monkeypatch) -> None:
    """Два duplicate skip подряд при уже сохранённых картинках → ранний done."""
    monkeypatch.setattr(
        "app.services.agent_orchestrator.settings.max_consecutive_tool_skips",
        2,
    )
    orchestrator = AgentOrchestrator(llm=MagicMock())
    ctx = TurnContext(
        conversation_id=__import__("uuid").uuid4(),
        user_message_id=__import__("uuid").uuid4(),
        emit=AsyncMock(),
        cancel_event=__import__("asyncio").Event(),
        llm_model=None,
        llm_messages=[],
        all_image_urls=["/media/asset/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
    )
    ctx.stream_draft.set_active_tool = AsyncMock()
    ctx.tool_state.before_tool(
        "generate_image",
        {"prompt": "cat"},
        cancel_event=ctx.cancel_event,
    )
    completion = LLMCompletion(
        content=None,
        tool_calls=[
            {
                "id": "call_dup",
                "type": "function",
                "function": {
                    "name": "generate_image",
                    "arguments": '{"prompt": "cat"}',
                },
            }
        ],
        finish_reason="tool_calls",
    )
    orchestrator._llm = MagicMock()
    orchestrator._llm.parse_tool_arguments = MagicMock(return_value={"prompt": "cat"})

    async def fake_complete(*_a, **_k):
        return MagicMock(
            assistant_text="ok",
            image_urls=ctx.all_image_urls,
            user_message=MagicMock(),
            assistant_message=MagicMock(),
        )

    orchestrator._complete_turn_after_anti_loop = AsyncMock(side_effect=fake_complete)

    turn_executor = MagicMock()
    result1 = await orchestrator._run_completion_tool_calls(
        ctx=ctx,
        completion=completion,
        turn_executor=turn_executor,
        round_idx=0,
    )
    assert result1 is None
    assert ctx.consecutive_tool_skips == 1

    result2 = await orchestrator._run_completion_tool_calls(
        ctx=ctx,
        completion=completion,
        turn_executor=turn_executor,
        round_idx=1,
    )
    assert result2 is not None
    orchestrator._complete_turn_after_anti_loop.assert_awaited_once()
    call_kw = orchestrator._complete_turn_after_anti_loop.await_args.kwargs
    assert call_kw["overflow_note"] is not None


def test_image_gen_prompt_discourages_repeat_calls() -> None:
    assert "без повторов" in IMAGE_GEN_PROMPT.lower() or "не вызывай" in IMAGE_GEN_PROMPT.lower()
    assert "count" in IMAGE_GEN_PROMPT
