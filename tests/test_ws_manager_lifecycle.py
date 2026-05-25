"""P1.7: WS reconnect и очистка busy/state."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from app.api.ws_manager import ConnectionManager


@pytest.mark.asyncio
async def test_reconnect_after_disconnect_not_busy() -> None:
    mgr = ConnectionManager()
    conv_id = uuid.uuid4()
    ws1 = AsyncMock()
    ws2 = AsyncMock()

    await mgr.connect(conv_id, ws1)
    assert not mgr.is_busy(conv_id)

    async def quick_turn() -> None:
        await asyncio.sleep(0.02)

    task = asyncio.create_task(quick_turn())
    mgr.set_active_task(conv_id, task)
    assert mgr.is_busy(conv_id)
    await task
    mgr.clear_active_task(conv_id)

    assert mgr.disconnect(conv_id, ws1) is True
    assert not mgr.is_busy(conv_id)

    await mgr.connect(conv_id, ws2)
    assert not mgr.is_busy(conv_id)
    assert mgr.websocket_count() == 1

    mgr.disconnect(conv_id, ws2)


@pytest.mark.asyncio
async def test_disconnect_does_not_cancel_running_task() -> None:
    """Обрыв WS (F5) не должен отменять фоновую генерацию."""
    mgr = ConnectionManager()
    conv_id = uuid.uuid4()
    ws = AsyncMock()
    await mgr.connect(conv_id, ws)

    cancel = mgr.reset_cancel(conv_id)
    started = asyncio.Event()

    async def long_turn() -> None:
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(long_turn())
    mgr.set_active_task(conv_id, task)
    await started.wait()
    assert mgr.is_busy(conv_id)
    assert not cancel.is_set()

    assert mgr.disconnect(conv_id, ws) is True
    assert mgr.is_busy(conv_id)
    assert not cancel.is_set()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    mgr.clear_active_task(conv_id)


@pytest.mark.asyncio
async def test_cancel_event_propagates() -> None:
    mgr = ConnectionManager()
    conv_id = uuid.uuid4()
    ws = AsyncMock()
    await mgr.connect(conv_id, ws)

    event = mgr.reset_cancel(conv_id)
    assert not event.is_set()
    mgr.cancel_turn(conv_id)
    assert event.is_set()

    mgr.disconnect(conv_id, ws)
