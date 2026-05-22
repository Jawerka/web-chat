"""
Контекст запроса для корреляции логов (беседа, тип хода).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator
from uuid import UUID

_conv_id: ContextVar[str | None] = ContextVar("log_conv_id", default=None)
_turn_kind: ContextVar[str | None] = ContextVar("log_turn_kind", default=None)


class LogContextFilter(logging.Filter):
    """Добавляет conv_id и turn в каждую запись журнала."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.conv_id = _conv_id.get() or "-"
        record.turn = _turn_kind.get() or "-"
        return True


@contextmanager
def log_turn_context(
    conversation_id: UUID | str | None,
    *,
    turn_kind: str = "turn",
) -> Iterator[None]:
    """Установить conv/turn для всех логов внутри блока."""
    conv_token = _conv_id.set(str(conversation_id) if conversation_id else None)
    turn_token = _turn_kind.set(turn_kind)
    try:
        yield
    finally:
        _conv_id.reset(conv_token)
        _turn_kind.reset(turn_token)
