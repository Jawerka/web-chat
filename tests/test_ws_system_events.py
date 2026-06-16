"""P1.3 / P1.7: системный WS /ws/events и broadcast."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.ws_events import broadcast_gallery_update, schedule_logs_append
from app.api.ws_manager import manager
from app.main import create_app
from tests.safety import safe_configure_database
from app.db.session import dispose_database, init_db


@pytest.fixture
def sync_client(tmp_path, monkeypatch):
    db_file = tmp_path / "sys_ws.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    async def _init() -> None:
        await dispose_database()
        safe_configure_database(db_url)
        await init_db()

    import asyncio as aio

    aio.run(_init())

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


def test_ws_events_ping(sync_client: TestClient) -> None:
    with sync_client.websocket_connect("/ws/events") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "connected"
        assert hello["channel"] == "system"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


@pytest.fixture(autouse=True)
def _reset_log_broadcast_state() -> None:
    from app.api import ws_events as we

    we._logs_batch = []
    we._logs_flush_task = None
    yield
    we._logs_batch = []
    we._logs_flush_task = None


@pytest.mark.asyncio
async def test_broadcast_gallery_update_delivered() -> None:
    ws = AsyncMock()
    await manager.connect_system(ws, subscriber_user_id=None)
    try:
        await broadcast_gallery_update("created", count=2)
        ws.send_json.assert_awaited()
        payload = ws.send_json.await_args.args[0]
        assert payload["type"] == "gallery_update"
        assert payload["reason"] == "created"
        assert payload["count"] == 2
    finally:
        manager.disconnect_system(ws)


@pytest.mark.asyncio
async def test_broadcast_gallery_update_user_scoped() -> None:
    import uuid

    uid_a = uuid.uuid4()
    uid_b = uuid.uuid4()
    ws_a = AsyncMock()
    ws_b = AsyncMock()
    await manager.connect_system(ws_a, subscriber_user_id=uid_a)
    await manager.connect_system(ws_b, subscriber_user_id=uid_b)
    try:
        await broadcast_gallery_update("created", kind="upload", count=1, user_id=uid_a)
        ws_a.send_json.assert_awaited()
        ws_b.send_json.assert_not_awaited()
    finally:
        manager.disconnect_system(ws_a)
        manager.disconnect_system(ws_b)


@pytest.mark.asyncio
async def test_schedule_logs_append_batches() -> None:
    ws = AsyncMock()
    manager._system_websockets.add(ws)
    try:
        schedule_logs_append("line-a")
        schedule_logs_append("line-b")
        await asyncio.sleep(0.55)
        assert ws.send_json.await_count >= 1
        payload = ws.send_json.await_args.args[0]
        assert payload["type"] == "logs_append"
        assert "line-a" in payload["lines"]
        assert "line-b" in payload["lines"]
    finally:
        manager.disconnect_system(ws)


def test_websocket_second_tab_busy(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """Две вкладки: вторая user_message при busy → code busy (P1.7)."""
    from tests.helpers import sync_api_create_conversation

    conv_id = sync_api_create_conversation(sync_client, test_conv_title)["id"]
    gate = asyncio.Event()

    async def slow_turn(*args, **kwargs):
        gate.set()
        await asyncio.sleep(2.0)
        from app.services.agent_orchestrator import AgentTurnResult

        return AgentTurnResult(assistant_text="ok")

    with patch(
        "app.api.websocket.AgentOrchestrator.run_conversation_turn",
        new=AsyncMock(side_effect=slow_turn),
    ):
        with sync_client.websocket_connect(f"/ws/{conv_id}") as ws1:
            ws1.receive_json()
            with sync_client.websocket_connect(f"/ws/{conv_id}") as ws2:
                ws2.receive_json()
                ws1.send_json(
                    {"type": "user_message", "text": "one", "attachment_ids": []},
                )
                import time

                deadline = time.monotonic() + 3.0
                while not gate.is_set():
                    if time.monotonic() > deadline:
                        break
                    time.sleep(0.02)
                ws2.send_json(
                    {"type": "user_message", "text": "two", "attachment_ids": []},
                )
                for _ in range(8):
                    msg = ws2.receive_json()
                    if msg.get("type") == "error" and msg.get("code") == "busy":
                        break
                else:
                    pytest.fail("expected busy error on second tab")
                assert msg["code"] == "busy"


def test_websocket_cancel_mid_turn(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """cancel во время turn → cancelled (P1.7)."""
    from tests.helpers import sync_api_create_conversation

    conv_id = sync_api_create_conversation(sync_client, test_conv_title)["id"]
    started = asyncio.Event()

    async def slow_turn(*args, **kwargs):
        emit = kwargs.get("emit") or args[3]
        cancel_event = kwargs.get("cancel_event") or args[4]
        started.set()
        await emit("ack", {"user_message_id": "00000000-0000-0000-0000-000000000001"})
        while not cancel_event.is_set():
            await asyncio.sleep(0.05)
        from app.services.agent_orchestrator import TurnCancelled

        raise TurnCancelled("Генерация отменена")

    with patch(
        "app.api.websocket.AgentOrchestrator.run_conversation_turn",
        new=AsyncMock(side_effect=slow_turn),
    ):
        with sync_client.websocket_connect(f"/ws/{conv_id}") as ws:
            ws.receive_json()
            ws.send_json(
                {"type": "user_message", "text": "wait", "attachment_ids": []},
            )
            import time

            for _ in range(50):
                msg = ws.receive_json()
                if msg.get("type") == "ack":
                    break
                time.sleep(0.02)
            ws.send_json({"type": "cancel"})
            for _ in range(20):
                msg = ws.receive_json()
                if msg.get("type") == "error" and msg.get("code") == "cancelled":
                    break
                time.sleep(0.02)
            else:
                pytest.fail("expected cancelled")
            assert msg["code"] == "cancelled"


def test_websocket_connected_shows_in_progress(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """После reconnect connected отражает in_progress (resume, P1.7)."""
    from tests.helpers import sync_api_create_conversation

    conv_id = sync_api_create_conversation(sync_client, test_conv_title)["id"]
    started = asyncio.Event()

    async def slow_turn(*args, **kwargs):
        started.set()
        await asyncio.sleep(1.5)
        from app.services.agent_orchestrator import AgentTurnResult

        return AgentTurnResult(assistant_text="done")

    with patch(
        "app.api.websocket.AgentOrchestrator.run_conversation_turn",
        new=AsyncMock(side_effect=slow_turn),
    ):
        with sync_client.websocket_connect(f"/ws/{conv_id}") as ws1:
            ws1.receive_json()
            ws1.send_json(
                {"type": "user_message", "text": "go", "attachment_ids": []},
            )
            import time

            for _ in range(50):
                if started.is_set():
                    break
                time.sleep(0.02)
            with sync_client.websocket_connect(f"/ws/{conv_id}") as ws2:
                hello = ws2.receive_json()
                assert hello["type"] == "connected"
                assert hello.get("in_progress") is True
