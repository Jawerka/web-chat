"""REST POST /turn для внешних интеграций."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.helpers import api_create_conversation


@pytest.mark.asyncio
async def test_start_turn_returns_202(client, test_conv_title: str) -> None:
    conv = await api_create_conversation(client, test_conv_title)
    conv_id = conv["id"]

    with patch(
        "app.api.websocket.AgentOrchestrator.run_conversation_turn",
        new=AsyncMock(),
    ):
        resp = await client.post(
            f"/api/conversations/{conv_id}/turn",
            json={"text": "из внешнего API", "attachment_ids": []},
        )
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"
    assert resp.json()["conversation_id"] == conv_id


@pytest.mark.asyncio
async def test_start_turn_busy_409(client, test_conv_title: str) -> None:
    from app.api.ws_manager import manager

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = conv["id"]
    import uuid

    cid = uuid.UUID(conv_id)

    async def hang(_ce):
        import asyncio

        await asyncio.sleep(60)

    import asyncio

    task = asyncio.create_task(hang(manager.reset_cancel(cid)))
    manager.set_active_task(cid, task)
    try:
        resp = await client.post(
            f"/api/conversations/{conv_id}/turn",
            json={"text": "должно быть занято"},
        )
        assert resp.status_code == 409
    finally:
        manager.cancel_turn(cid)
        task.cancel()
        manager.clear_active_task(cid)
