"""P0.4: user-сообщение сохраняется до LLM даже при ошибке хода."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.websocket import _run_turn_task
from app.db import session as db_session
from app.db.models import MessageRole
from app.db.repositories import MessageRepository
from app.integrations.llm_client import LLMError
from app.services.agent_orchestrator import AgentOrchestrator
from app.services.turn_recovery import settle_interrupted_turn
from tests.helpers import api_create_conversation


@pytest.mark.asyncio
async def test_user_message_committed_before_llm_error(
    client,
    test_conv_title: str,
) -> None:
    """После LLMError user-сообщение остаётся в БД (commit до вызова LLM)."""
    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    mock_llm = MagicMock()
    mock_llm.parse_tool_arguments = MagicMock(return_value={})
    mock_llm.complete_with_stream = AsyncMock(side_effect=LLMError("LLM недоступен"))

    emitted: list[str] = []

    async def emit(event_type: str, _payload: dict) -> None:
        emitted.append(event_type)

    orchestrator = AgentOrchestrator(llm=mock_llm)

    async with db_session.async_session_factory() as session:
        with pytest.raises(LLMError):
            await orchestrator.run_conversation_turn(
                session,
                conv_id,
                "Сообщение перед сбоем LLM",
                [],
                emit,
                asyncio.Event(),
            )

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        messages = await msg_repo.list_for_conversation(conv_id)
        users = [m for m in messages if m.role == MessageRole.USER]
        assert len(users) >= 1
        assert any("Сообщение перед сбоем" in (m.content_text or "") for m in users)

        await settle_interrupted_turn(
            session,
            conv_id,
            status_code="llm_error",
            status_message="LLM недоступен",
        )
        await session.commit()

    resp = await client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert any(
        m["role"] == "user" and "Сообщение перед сбоем" in (m.get("content_text") or "")
        for m in data
    )


@pytest.mark.asyncio
async def test_run_turn_task_keeps_user_on_llm_error(
    client,
    test_conv_title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Фоновый WS turn: user остаётся в REST после LLMError."""
    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    mock_llm = MagicMock()
    mock_llm.parse_tool_arguments = MagicMock(return_value={})
    mock_llm.complete_with_stream = AsyncMock(side_effect=LLMError("timeout"))

    monkeypatch.setattr(
        "app.api.websocket.LLMClient",
        lambda *a, **k: mock_llm,
    )

    await _run_turn_task(
        conv_id,
        "WS ход при ошибке",
        [],
        asyncio.Event(),
    )

    resp = await client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    assert any(
        m["role"] == "user" and "WS ход" in (m.get("content_text") or "")
        for m in resp.json()
    )
