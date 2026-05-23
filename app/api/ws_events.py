"""Вспомогательные WS-события (P1.3)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.api.ws_manager import manager
from app.db import session as db_session
from app.services.generation_state import get_generation_state

logger = logging.getLogger(__name__)

_logs_batch: list[str] = []
_logs_flush_task: asyncio.Task[None] | None = None
_LOGS_FLUSH_INTERVAL_SEC = 0.4


async def emit_progress(conversation_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Прогресс хода: подпись, этап, опционально процент (SD / LLM)."""
    manager.set_progress(conversation_id, payload)
    await manager.send_json(
        conversation_id,
        {"type": "progress", **payload},
    )


async def broadcast_generation_update(conversation_id: uuid.UUID) -> None:
    """Отправить актуальное состояние генерации всем вкладкам беседы."""
    async with db_session.async_session_factory() as session:
        state = await get_generation_state(session, conversation_id)
    await manager.send_json(
        conversation_id,
        {"type": "generation_update", **state},
    )


async def broadcast_gallery_update(
    reason: str,
    *,
    asset_id: str | None = None,
    count: int | None = None,
) -> None:
    """Уведомить подписчиков /ws/events об изменении галереи."""
    payload: dict[str, Any] = {"type": "gallery_update", "reason": reason}
    if asset_id is not None:
        payload["asset_id"] = asset_id
    if count is not None:
        payload["count"] = count
    await manager.broadcast_system(payload)


async def _flush_logs_batch() -> None:
    global _logs_batch, _logs_flush_task
    _logs_flush_task = None
    if not _logs_batch:
        return
    lines = _logs_batch
    _logs_batch = []
    await manager.broadcast_system({"type": "logs_append", "lines": lines})


def schedule_logs_append(line: str) -> None:
    """Поставить строку лога в очередь WS (из sync logging.Handler)."""
    global _logs_flush_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _logs_batch.append(line)
    if _logs_flush_task is not None and not _logs_flush_task.done():
        return
    _logs_flush_task = loop.create_task(_delayed_logs_flush())


async def _delayed_logs_flush() -> None:
    try:
        await asyncio.sleep(_LOGS_FLUSH_INTERVAL_SEC)
        await _flush_logs_batch()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("logs_append broadcast failed")
