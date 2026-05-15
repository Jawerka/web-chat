"""
Кольцевой буфер логов приложения для отдачи в UI.
"""

from __future__ import annotations

import logging
from collections import deque
from threading import Lock

_BUFFER: deque[str] = deque(maxlen=500)
_LOCK = Lock()
_HANDLER: logging.Handler | None = None


class RingBufferHandler(logging.Handler):
    """Сохраняет отформатированные записи в памяти."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with _LOCK:
            _BUFFER.append(line)


def install_log_buffer() -> None:
    """Подключить буфер к корневому логгеру (один раз)."""
    global _HANDLER
    if _HANDLER is not None:
        return
    handler = RingBufferHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logging.getLogger().addHandler(handler)
    _HANDLER = handler


def get_log_lines(*, limit: int = 200) -> list[str]:
    """Последние limit строк серверного журнала."""
    with _LOCK:
        lines = list(_BUFFER)
    if limit < 1:
        return []
    return lines[-limit:]


def clear_log_buffer() -> None:
    """Очистить серверный буфер."""
    with _LOCK:
        _BUFFER.clear()
