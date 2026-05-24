"""Тесты graceful shutdown (BE-2)."""

from __future__ import annotations

import pytest

from app.services.job_queue import HeavyJobQueue, ShutdownInProgress


@pytest.mark.asyncio
async def test_job_queue_rejects_after_begin_shutdown() -> None:
    q = HeavyJobQueue(workers=1)
    await q.start()
    q.begin_shutdown()

    with pytest.raises(ShutdownInProgress):
        await q.run_sync(lambda: 1, operation="test")

    await q.stop(drain_timeout=0.1)
