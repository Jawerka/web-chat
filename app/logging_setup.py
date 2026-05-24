"""
Настройка журналирования: консоль, файл, кольцевой буфер UI.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings
from app.log_context import LogContextFilter
from app.logging_buffer import install_log_buffer

_LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] conv=%(conv_id)s turn=%(turn)s "
    "ws=%(ws_session)s %(message)s"
)
_CONFIGURED = False


class JsonLogFormatter(logging.Formatter):
    """Структурированная строка лога (P1.6)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "conv_id": getattr(record, "conv_id", "-"),
            "turn": getattr(record, "turn", "-"),
            "ws_session": getattr(record, "ws_session", "-"),
        }
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        for key in ("event", "error_id", "error_code", "tool", "duration_ms", "user_id"):
            val = getattr(record, key, None)
            if val is not None and val != "":
                payload[key] = val
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    """Инициализировать корневой логгер (один раз)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    ctx_filter = LogContextFilter()
    formatter: logging.Formatter
    if settings.log_json:
        formatter = JsonLogFormatter()
    else:
        formatter = logging.Formatter(_LOG_FORMAT)

    if not root.handlers:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.addFilter(ctx_filter)
        root.addHandler(console)

    log_path = settings.log_file.strip()
    if os.environ.get("WEB_CHAT_DISABLE_LOG_FILE") or "pytest" in sys.modules:
        log_path = ""
    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ctx_filter)
        root.addHandler(file_handler)

    install_log_buffer(formatter=formatter, ctx_filter=ctx_filter)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    _CONFIGURED = True
