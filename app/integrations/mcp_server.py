"""
Встроенный MCP-сервер (FastMCP, streamable-http) в фоновом потоке.

Порт по умолчанию: WEB_PORT + 1 (например 8091 при web 8090).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from app.config import settings
from app.integrations.document_tools import register_document_tools
from app.integrations.sd_tools import register_sd_tools

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

mcp = FastMCP("web-chat")
register_sd_tools(mcp)
register_document_tools(mcp)


def _run_mcp() -> None:
    """Запуск MCP в текущем потоке (daemon)."""
    port = settings.effective_mcp_port
    os.environ["FASTMCP_PORT"] = str(port)
    logger.info(
        "MCP streamable-http на %s:%d/mcp (timeout=%ds)",
        settings.web_host,
        port,
        settings.mcp_timeout,
    )
    mcp.run(
        transport="streamable-http",
        host=settings.web_host,
        port=port,
    )


def start_mcp_background() -> threading.Thread:
    """
    Запустить MCP в фоновом daemon-потоке.

    Returns:
        Поток с запущенным MCP (завершится вместе с процессом).
    """
    thread = threading.Thread(target=_run_mcp, name="mcp-server", daemon=True)
    thread.start()
    return thread
