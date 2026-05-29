"""P4.4: integration/load — WS reconnect, concurrent WS, img2img/upscale (mock SD)."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import session as db_session
from app.db.session import dispose_database, init_db
from app.integrations.tool_executor import ToolExecutor
from app.main import create_app
from app.services.agent_orchestrator import AgentTurnResult
from tests.helpers import sync_api_create_conversation
from tests.safety import safe_configure_database

_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def _run_sync_inline(fn, /, *args, cancel_event=None, operation="", **kwargs):
    """In-process run_sync для тестов (без worker thread / event loop clash)."""
    if cancel_event is not None and cancel_event.is_set():
        from app.services.job_queue import JobCancelled

        raise JobCancelled()
    return fn(*args, **kwargs)


@pytest.fixture
def sync_client(tmp_path, monkeypatch):
    """TestClient + изолированная SQLite (как test_websocket)."""
    db_file = tmp_path / "load_ws.sqlite"
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


@pytest.mark.load
def test_ws_reconnect_after_disconnect_idle(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """Reconnect после закрытия WS: connected, не busy, ping/pong."""
    conv_id = sync_api_create_conversation(sync_client, test_conv_title)["id"]

    with sync_client.websocket_connect(f"/ws/{conv_id}") as ws1:
        assert ws1.receive_json()["type"] == "connected"

    with sync_client.websocket_connect(f"/ws/{conv_id}") as ws2:
        hello = ws2.receive_json()
        assert hello["type"] == "connected"
        assert hello.get("in_progress") is False
        ws2.send_json({"type": "ping"})
        assert ws2.receive_json()["type"] == "pong"


@pytest.mark.load
def test_concurrent_ws_four_connections_ping(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """Четыре одновременных WS на одну беседу — все получают connected и pong."""
    conv_id = sync_api_create_conversation(sync_client, test_conv_title)["id"]

    with ExitStack() as stack:
        sockets = [
            stack.enter_context(sync_client.websocket_connect(f"/ws/{conv_id}"))
            for _ in range(4)
        ]
        for ws in sockets:
            assert ws.receive_json()["type"] == "connected"
        for ws in sockets:
            ws.send_json({"type": "ping"})
        for ws in sockets:
            assert ws.receive_json()["type"] == "pong"


@pytest.mark.load
def test_ws_reconnect_during_turn_shows_in_progress(
    sync_client: TestClient,
    test_conv_title: str,
) -> None:
    """Вторая вкладка во время хода — connected.in_progress=true (§22 load)."""
    conv_id = sync_api_create_conversation(sync_client, test_conv_title)["id"]
    started = asyncio.Event()

    async def slow_turn(*args, **kwargs):
        started.set()
        await asyncio.sleep(1.2)
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
            deadline = time.monotonic() + 3.0
            while not started.is_set():
                if time.monotonic() > deadline:
                    break
                time.sleep(0.02)
            with sync_client.websocket_connect(f"/ws/{conv_id}") as ws2:
                hello = ws2.receive_json()
                assert hello["type"] == "connected"
                assert hello.get("in_progress") is True


@pytest.mark.asyncio
@pytest.mark.load
async def test_img2img_tool_executor_mock_sd(
    client,
    test_conv_title: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """img2img через ToolExecutor с mock SD (без WebUI)."""
    from tests.helpers import api_create_conversation

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    gen = tmp_path / "generated"
    gen.mkdir()
    (gen / "sd_test.png").write_bytes(_MINIMAL_PNG)
    monkeypatch.setattr("app.integrations.media_utils.GENERATED_ROOT", gen)

    def fake_img2img(**_kwargs: object) -> str:
        return "img2img ok\n/media/generated/out.png"

    monkeypatch.setattr("app.integrations.tool_executor.img2img", fake_img2img)
    monkeypatch.setattr(
        "app.integrations.tool_executor.heavy_job_queue.run_sync",
        _run_sync_inline,
    )

    async with db_session.async_session_factory() as session:
        executor = ToolExecutor(session, conversation_id=conv_id)
        result = await executor.run(
            "img2img",
            {
                "prompt": "test",
                "init_image_url": "sd_test.png",
                "denoising_strength": 0.5,
            },
        )

    assert "img2img ok" in result.content


@pytest.mark.asyncio
@pytest.mark.load
async def test_upscale_images_tool_executor_mock_sd(
    client,
    test_conv_title: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """upscale_images через ToolExecutor с mock SD."""
    from tests.helpers import api_create_conversation

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    gen = tmp_path / "generated"
    gen.mkdir()
    (gen / "sd_test.png").write_bytes(_MINIMAL_PNG)
    monkeypatch.setattr("app.integrations.media_utils.GENERATED_ROOT", gen)

    def fake_upscale(**_kwargs: object) -> str:
        return "Upscaled 1 image(s)"

    monkeypatch.setattr("app.integrations.tool_executor.upscale_images", fake_upscale)
    monkeypatch.setattr(
        "app.integrations.tool_executor.heavy_job_queue.run_sync",
        _run_sync_inline,
    )

    async with db_session.async_session_factory() as session:
        executor = ToolExecutor(session, conversation_id=conv_id)
        result = await executor.run(
            "upscale_images",
            {"file_urls": ["sd_test.png"]},
        )

    assert "Upscaled" in result.content
