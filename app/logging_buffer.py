"""
Кольцевой буфер логов приложения для отдачи в UI.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from threading import Lock

_BUFFER: deque[str] = deque(maxlen=8000)
_CLIENT_BUFFER: deque[str] = deque(maxlen=4000)
_LOCK = Lock()
_HANDLER: logging.Handler | None = None
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


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
        try:
            from app.api.ws_events import schedule_logs_append

            schedule_logs_append(line)
        except Exception:
            pass


def set_main_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Сохранить event loop приложения для WS-рассылки логов из sync-кода."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def get_main_event_loop() -> asyncio.AbstractEventLoop | None:
    return _MAIN_LOOP


def _attach_handler_to_logger(logger_name: str) -> None:
    """Подключить ring buffer к именованному логгеру."""
    if _HANDLER is None:
        return
    log = logging.getLogger(logger_name)
    if _HANDLER not in log.handlers:
        log.addHandler(_HANDLER)


def ensure_log_buffer_attached() -> None:
    """Переподключить буфер к root и uvicorn (uvicorn может сбросить handlers)."""
    global _HANDLER
    if _HANDLER is None:
        install_log_buffer()
        return
    root = logging.getLogger()
    if _HANDLER not in root.handlers:
        root.addHandler(_HANDLER)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        _attach_handler_to_logger(name)


def install_log_buffer(
    *,
    formatter: logging.Formatter | None = None,
    ctx_filter: logging.Filter | None = None,
) -> None:
    """Подключить буфер к корневому логгеру (один раз)."""
    global _HANDLER
    if _HANDLER is not None:
        ensure_log_buffer_attached()
        return
    handler = RingBufferHandler()
    handler.setLevel(logging.getLogger().level or logging.INFO)
    handler.setFormatter(
        formatter
        or logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] conv=%(conv_id)s turn=%(turn)s "
            "ws=%(ws_session)s %(message)s",
        ),
    )
    if ctx_filter is not None:
        handler.addFilter(ctx_filter)
    logging.getLogger().addHandler(handler)
    _HANDLER = handler
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        _attach_handler_to_logger(name)


def append_client_log_lines(lines: list[str]) -> int:
    """Добавить строки клиентского журнала (браузер → сервер)."""
    added = 0
    with _LOCK:
        for raw in lines:
            line = str(raw or "").strip()
            if not line:
                continue
            _CLIENT_BUFFER.append(line)
            added += 1
    return added


def get_log_lines(*, limit: int = 200) -> list[str]:
    """Последние limit строк серверного журнала."""
    with _LOCK:
        lines = list(_BUFFER)
    if limit < 1:
        return []
    return lines[-limit:]


def get_client_log_lines(*, limit: int = 200) -> list[str]:
    """Последние limit строк клиентского журнала."""
    with _LOCK:
        lines = list(_CLIENT_BUFFER)
    if limit < 1:
        return []
    return lines[-limit:]


def clear_log_buffer() -> None:
    """Очистить серверный буфер."""
    with _LOCK:
        _BUFFER.clear()
