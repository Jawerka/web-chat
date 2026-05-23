"""Вспомогательные WS-события (P1.3)."""

from __future__ import annotations

import uuid
from typing import Any

from app.api.ws_manager import manager
from app.db import session as db_session
from app.services.generation_state import get_generation_state


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
