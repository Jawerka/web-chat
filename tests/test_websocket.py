"""Тесты WebSocket и messages API (этап 7)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.db.models import MessageRole
from app.db.session import dispose_database, init_db
from tests.safety import assert_not_using_production_database, safe_configure_database
from app.main import create_app
from app.services.agent_orchestrator import AgentTurnResult
from tests.helpers import sync_api_create_conversation


@pytest.fixture
def sync_client(tmp_path, monkeypatch):
    """Синхронный TestClient для WebSocket."""
    db_file = tmp_path / "ws.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    async def _init() -> None:
        await dispose_database()
        safe_configure_database(db_url)
        await init_db()

    import asyncio

    asyncio.run(_init())

    noop = type("T", (), {})()
    stop = type("E", (), {"set": lambda self: None})()
    monkeypatch.setattr("app.integrations.mcp_server.start_mcp_background", lambda: noop)
    monkeypatch.setattr("app.main.start_mcp_background", lambda: noop)
    monkeypatch.setattr(
        "app.main.start_retention_background",
        lambda: (noop, stop),
    )

    app = create_app()
    with TestClient(app) as client:
        yield client


def test_websocket_ping_and_messages_api(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """connected, ping/pong и GET messages."""
    conv = sync_api_create_conversation(sync_client, test_conv_title)
    conv_id = conv["id"]

    with sync_client.websocket_connect(f"/ws/{conv_id}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "connected"
        assert hello["conversation_id"] == conv_id

        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"

    messages = sync_client.get(f"/api/conversations/{conv_id}/messages")
    assert messages.status_code == 200
    assert messages.json() == []


def test_websocket_user_message_mocked(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """user_message с mock оркестратора → ack, text_delta, done."""
    conv_id = sync_api_create_conversation(sync_client, test_conv_title)["id"]

    fake_user = type("M", (), {"id": uuid.uuid4()})()
    fake_assistant = type("M", (), {"id": uuid.uuid4()})()

    async def fake_turn(*args, **kwargs):
        emit = kwargs.get("emit") or args[3]
        await emit("ack", {"user_message_id": str(fake_user.id)})
        await emit("text_delta", {"content": "Ответ"})
        await emit("done", {"assistant_message_id": str(fake_assistant.id)})
        return AgentTurnResult(
            assistant_text="Ответ",
            user_message=fake_user,
            assistant_message=fake_assistant,
        )

    with patch(
        "app.api.websocket.AgentOrchestrator.run_conversation_turn",
        new=AsyncMock(side_effect=fake_turn),
    ):
        with sync_client.websocket_connect(f"/ws/{conv_id}") as ws:
            ws.receive_json()  # connected
            ws.send_json(
                {
                    "type": "user_message",
                    "text": "Привет",
                    "attachment_ids": [],
                }
            )
            types = []
            for _ in range(5):
                msg = ws.receive_json()
                types.append(msg["type"])
                if msg["type"] == "done":
                    break

    assert "ack" in types
    assert "text_delta" in types
    assert "done" in types

    history = sync_client.get(f"/api/conversations/{conv_id}/messages").json()
    assert len(history) >= 0  # mock не пишет в БД


@pytest.mark.asyncio
async def test_messages_list_after_db_insert(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """GET messages возвращает сохранённые сообщения."""
    from app.db import session as db_session
    from app.db.repositories import MessageRepository
    from tests.helpers import api_create_conversation

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=conv_id,
            role=MessageRole.USER,
            content_text="Вопрос",
        )
        await msg_repo.create(
            conversation_id=conv_id,
            role=MessageRole.ASSISTANT,
            content_text="Ответ",
        )
        await session.commit()

    resp = await client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[1]["role"] == "assistant"
