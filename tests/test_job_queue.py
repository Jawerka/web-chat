"""P1.2: очередь тяжёлых операций."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.services.job_queue import HeavyJobQueue, JobCancelled


@pytest.mark.asyncio
async def test_heavy_job_queue_runs_sync_function() -> None:
    queue = HeavyJobQueue(workers=1)
    await queue.start()
    try:
        result = await queue.run_sync(lambda x: x + 1, 41, operation="add")
        assert result == 42
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_heavy_job_queue_respects_cancel_before_start() -> None:
    queue = HeavyJobQueue(workers=1)
    await queue.start()
    cancel = asyncio.Event()
    cancel.set()
    try:
        with pytest.raises(JobCancelled):
            await queue.run_sync(
                time.sleep,
                5,
                cancel_event=cancel,
                operation="sleep",
            )
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_heavy_job_queue_serializes_with_one_worker() -> None:
    queue = HeavyJobQueue(workers=1)
    await queue.start()
    order: list[int] = []

    def work(n: int) -> int:
        order.append(n)
        time.sleep(0.05)
        return n

    try:
        results = await asyncio.gather(
            queue.run_sync(work, 1, operation="a"),
            queue.run_sync(work, 2, operation="b"),
        )
        assert results == [1, 2]
        assert order == [1, 2]
    finally:
        await queue.stop()
