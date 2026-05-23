"""
Настройка журналирования: консоль, файл, кольцевой буфер UI.
"""

from __future__ import annotations

import logging
import os
import sys
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


def setup_logging() -> None:
    """Инициализировать корневой логгер (один раз)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    ctx_filter = LogContextFilter()
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
