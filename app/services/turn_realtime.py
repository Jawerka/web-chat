"""
Порты realtime-состояния хода (P3.6).

Сервисный слой не импортирует `app.api.*`; реализация подключается при старте приложения.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

ProgressEmitter = Callable[[uuid.UUID, dict[str, Any]], Awaitable[None]]


class ConversationTurnRealtime(Protocol):
    """Состояние стрима/прогресса беседы (реализует ConnectionManager)."""

    def set_streaming_message(
        self,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
    ) -> None: ...

    def get_streaming_message(self, conversation_id: uuid.UUID) -> uuid.UUID | None: ...

    def clear_streaming_message(self, conversation_id: uuid.UUID) -> None: ...

    def clear_progress(self, conversation_id: uuid.UUID) -> None: ...


_realtime: ConversationTurnRealtime | None = None
_progress_emit: ProgressEmitter | None = None


def configure_turn_realtime(
    realtime: ConversationTurnRealtime,
    *,
    progress_emit: ProgressEmitter,
) -> None:
    """Вызвать из lifespan приложения (см. app.main)."""
    global _realtime, _progress_emit
    _realtime = realtime
    _progress_emit = progress_emit


def _default_realtime() -> ConversationTurnRealtime:
    from app.api.ws_manager import manager

    return manager


def turn_realtime() -> ConversationTurnRealtime:
    """Активная реализация портов (manager после configure_turn_realtime)."""
    if _realtime is not None:
        return _realtime
    return _default_realtime()


async def emit_turn_progress(
    conversation_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    """WS progress для хода агента."""
    if _progress_emit is not None:
        await _progress_emit(conversation_id, payload)
        return
    from app.api.ws_events import emit_progress

    await emit_progress(conversation_id, payload)
