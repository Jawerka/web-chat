"""
Фоновая периодическая очистка по retention (этап 10).
"""

from __future__ import annotations

import asyncio
import logging

from app.db import session as db_session
from app.services.cleanup_service import run_full_cleanup

logger = logging.getLogger(__name__)

_CLEANUP_INTERVAL_SEC = 24 * 3600


async def retention_loop(stop_event: asyncio.Event) -> None:
    """
    Цикл очистки: сразу при старте, затем раз в сутки.

    Останавливается по stop_event при shutdown приложения.
    """
    while not stop_event.is_set():
        try:
            await _run_once()
        except Exception:
            logger.exception("Ошибка фоновой очистки retention")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_CLEANUP_INTERVAL_SEC)
            break
        except TimeoutError:
            continue


async def _run_once() -> None:
    """Один проход очистки в отдельной сессии БД."""
    async with db_session.async_session_factory() as session:
        stats = await run_full_cleanup(session)
        await session.commit()
    if any(stats.values()):
        logger.info("Retention cleanup: %s", stats)


def start_retention_background() -> tuple[asyncio.Task[None], asyncio.Event]:
    """Запустить фоновую задачу очистки; вернуть (task, stop_event)."""
    stop = asyncio.Event()
    task = asyncio.create_task(retention_loop(stop), name="retention-cleanup")
    return task, stop
