"""
Регрессия из BACKLOG.md § «Регрессия после каждой фазы».
"""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient

from app.api.ws_manager import ConnectionManager
from app.integrations.tool_executor import ToolExecutor, ToolResult
from app.services.agent_orchestrator import AgentTurnResult
from app.services.streaming_draft import AssistantStreamDraft
from tests.helpers import sync_api_create_conversation


@pytest.mark.asyncio
async def test_health_public_base_url_follows_request_host(client: AsyncClient) -> None:
    """PUBLIC_BASE_URL в /api/health совпадает с Host браузера (LAN)."""
    r = await client.get(
        "/api/health",
        headers={"Host": "192.168.88.44:8090"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["public_base_url"] == "http://192.168.88.44:8090"
    assert data["public_base_url_lan"] == "http://192.168.88.44:8090"


@pytest.mark.asyncio
async def test_img2img_regenerate_logs_init_from_user_message() -> None:
    """img2img regenerate: fallback init из user-сообщения (лог для журнала)."""
    msg_id = uuid.uuid4()
    session = AsyncMock()
    executor = ToolExecutor(session, source_user_message_id=msg_id)
    calls = [0]

    async def pinned():
        calls[0] += 1
        return None if calls[0] == 1 else (b"png", "user.png")

    executor._get_pinned_user_init = pinned
    executor._load_init_image = AsyncMock(side_effect=FileNotFoundError("bad url"))
    executor._run_sd_image_tool = AsyncMock(
        return_value=ToolResult(content="ok", image_urls=[]),
    )

    with patch("app.integrations.tool_executor.logger") as log:
        await executor._img2img(
            {"prompt": "test", "init_image_url": "http://bad.example/x.png"},
        )
        logged = " ".join(str(c) for c in log.info.call_args_list)
    assert "init взят из user-сообщения" in logged


@pytest.mark.asyncio
async def test_stream_draft_add_images_dedupes_for_f5_resume(
    tmp_path,
    repo_conv_title: str,
) -> None:
    """SD → F5: сервер не дублирует URL в черновике; UI _setGridImages дедуплирует при resume."""
    from app.db import session as db_session
    from app.db.models import MessageRole
    from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository
    from app.db.session import dispose_database, init_db
    from tests.safety import assert_not_using_production_database, safe_configure_database

    await dispose_database()
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dedupe.sqlite'}"
    safe_configure_database(db_url)
    await init_db()
    assert_not_using_production_database()

    emitted: list[tuple[str, dict]] = []

    async def emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    url = "/media/asset/550e8400-e29b-41d4-a716-446655440000"
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(
            title=repo_conv_title,
            preset_id=preset.id,
        )
        msg_repo = MessageRepository(session)
        draft = AssistantStreamDraft(session, msg_repo, ConversationRepository(session), conv, emit)
        await draft.on_delta("gen")
        await draft.add_images([url, url], ["550e8400-e29b-41d4-a716-446655440000"])
        await session.commit()
        cj = draft.message.content_json or {}
        assert cj.get("images") == [url]


@pytest.fixture
def sync_client(tmp_path, monkeypatch):
    """Sync TestClient с изолированной БД (как test_websocket)."""
    from app.db.session import dispose_database, init_db
    from app.main import create_app
    from tests.safety import assert_not_using_production_database, safe_configure_database

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'reg.sqlite'}"

    async def _init() -> None:
        await dispose_database()
        safe_configure_database(db_url)
        await init_db()
        assert_not_using_production_database()

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


def test_ws_disconnect_after_turn_not_busy(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """После обрыва WS и завершения хода is_busy снимается (нет вечной генерации)."""
    conv_id = sync_api_create_conversation(sync_client, test_conv_title)["id"]
    done = asyncio.Event()

    async def quick_turn(*args, **kwargs):
        await asyncio.sleep(0.15)
        done.set()
        return AgentTurnResult(assistant_text="ok")

    with patch(
        "app.api.websocket.AgentOrchestrator.run_conversation_turn",
        new=AsyncMock(side_effect=quick_turn),
    ):
        with sync_client.websocket_connect(f"/ws/{conv_id}") as ws:
            ws.receive_json()
            ws.send_json({"type": "user_message", "text": "hi", "attachment_ids": []})
        for _ in range(80):
            if done.is_set():
                break
            time.sleep(0.02)
        assert done.is_set()
        st = sync_client.get(f"/api/conversations/{conv_id}/generation-status").json()
        assert st["in_progress"] is False


@pytest.mark.asyncio
async def test_reconnect_manager_not_busy_after_task_cleared() -> None:
    """Менеджер WS: disconnect не оставляет is_busy после clear_active_task."""
    mgr = ConnectionManager()
    conv_id = uuid.uuid4()
    ws = AsyncMock()
    await mgr.connect(conv_id, ws)

    async def turn() -> None:
        await asyncio.sleep(0.05)

    task = asyncio.create_task(turn())
    mgr.set_active_task(conv_id, task)
    await task
    mgr.clear_active_task(conv_id)
    mgr.disconnect(conv_id, ws)
    assert not mgr.is_busy(conv_id)
