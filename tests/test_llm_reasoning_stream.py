"""reasoning_delta: извлечение фрагментов из stream delta."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.llm_client import LLMClient, _stream_delta_reasoning


def test_stream_delta_reasoning_content() -> None:
    delta = SimpleNamespace(reasoning_content="think step")
    assert _stream_delta_reasoning(delta) == "think step"


def test_stream_delta_reasoning_alias() -> None:
    delta = SimpleNamespace(reasoning="chain")
    assert _stream_delta_reasoning(delta) == "chain"


def test_stream_delta_reasoning_empty() -> None:
    assert _stream_delta_reasoning(SimpleNamespace()) is None


@pytest.mark.asyncio
async def test_complete_with_stream_emits_reasoning() -> None:
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = SimpleNamespace(
        content=None,
        reasoning_content="alpha ",
        tool_calls=None,
    )
    chunk.choices[0].finish_reason = None

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta = SimpleNamespace(
        content="hi",
        reasoning_content="beta",
        tool_calls=None,
    )
    chunk2.choices[0].finish_reason = "stop"

    async def fake_stream():
        yield chunk
        yield chunk2

    client = LLMClient()
    client.resolve_model = AsyncMock(return_value="test-model")  # type: ignore[method-assign]
    client._client = MagicMock()
    client._client.chat.completions.create = AsyncMock(return_value=fake_stream())

    reasoning_chunks: list[str] = []
    text_chunks: list[str] = []

    async def on_reasoning(piece: str) -> None:
        reasoning_chunks.append(piece)

    async def on_text(piece: str) -> None:
        text_chunks.append(piece)

    result = await client.complete_with_stream(
        [{"role": "user", "content": "q"}],
        on_text_delta=on_text,
        on_reasoning_delta=on_reasoning,
    )

    assert reasoning_chunks == ["alpha ", "beta"]
    assert text_chunks == ["hi"]
    assert result.reasoning == "alpha beta"
