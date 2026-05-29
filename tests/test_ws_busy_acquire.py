"""P3.5: атомарный захват хода (try_start_turn)."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.api.ws_manager import ConnectionManager
from app.errors import ErrorCode


@pytest.mark.asyncio
async def test_try_start_turn_rejects_second_concurrent() -> None:
    """Второй try_start_turn на ту же беседу → False, пока первая задача жива."""
    mgr = ConnectionManager()
    conv_id = uuid.uuid4()
    gate = asyncio.Event()

    async def long_runner(_cancel: asyncio.Event) -> None:
        await gate.wait()

    assert mgr.try_start_turn(conv_id, long_runner, turn_kind="test") is True
    assert mgr.is_busy(conv_id) is True
    assert mgr.try_start_turn(conv_id, long_runner, turn_kind="test") is False

    gate.set()
    state = mgr._sessions.get(conv_id)
    assert state is not None
    task = state.active_task
    assert task is not None
    await asyncio.wait_for(task, timeout=5.0)
    assert mgr.is_busy(conv_id) is False


@pytest.mark.asyncio
async def test_try_start_turn_allows_after_task_done() -> None:
    mgr = ConnectionManager()
    conv_id = uuid.uuid4()

    async def quick(_cancel: asyncio.Event) -> None:
        return

    assert mgr.try_start_turn(conv_id, quick, turn_kind="a") is True
    state = mgr._sessions[conv_id]
    assert state.active_task is not None
    await asyncio.wait_for(state.active_task, timeout=5.0)
    assert mgr.is_busy(conv_id) is False
    assert mgr.try_start_turn(conv_id, quick, turn_kind="b") is True
