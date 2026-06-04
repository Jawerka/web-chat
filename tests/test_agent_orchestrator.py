"""Тесты оркестратора агента (mock LLM)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.llm_client import LLMCompletion
from app.services.agent_orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_run_turn_text_only() -> None:
    """Без tool_calls — сразу текст ответа."""
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMCompletion(
            content="Привет!",
            tool_calls=[],
            finish_reason="stop",
        )
    )
    orchestrator = AgentOrchestrator(llm=mock_llm)
    result = await orchestrator.run_turn("Привет")
    assert result.assistant_text == "Привет!"
    mock_llm.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_turn_with_tool_call() -> None:
    """Один tool_call → второй ответ с текстом."""
    mock_llm = MagicMock()
    mock_llm.parse_tool_arguments = MagicMock(return_value={"prompt": "cat"})
    mock_llm.complete = AsyncMock(
        side_effect=[
            LLMCompletion(
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "generate_image",
                            "arguments": '{"prompt": "cat"}',
                        },
                    }
                ],
                finish_reason="tool_calls",
            ),
            LLMCompletion(
                content="Вот картинка кота.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    # canonical_stored_image_urls отбрасывает /media/generated/ (после ingest — только asset)
    asset_url = "/media/asset/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    mock_tools = MagicMock()
    mock_tools.run = AsyncMock(
        return_value=MagicMock(
            content=f"URL: {asset_url}",
            image_urls=[asset_url],
        )
    )
    orchestrator = AgentOrchestrator(llm=mock_llm, tool_executor=mock_tools)
    result = await orchestrator.run_turn("Нарисуй кота", system_prompt="test")
    assert "кота" in result.assistant_text
    assert result.image_urls == [asset_url]
    assert mock_llm.complete.await_count == 2


def test_merge_streamed_llm_text() -> None:
    merge = AgentOrchestrator._merge_streamed_llm_text
    assert merge("полный текст", "полный текст") == "полный текст"
    assert merge("до tool", "до tool\n\nпосле") == "до tool\n\nпосле"
    assert merge("длинный буфер из стрима", "корот") == "длинный буфер из стрима"
    assert merge("", "только llm") == "только llm"
    assert merge("только буфер", None) == "только буфер"
    assert merge("часть A", "часть B") == "часть A\n\nчасть B"
