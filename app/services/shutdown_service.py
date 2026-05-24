"""
Корректное завершение процесса (BE-2): WS, очередь heavy jobs, логи.
"""

from __future__ import annotations

import logging

from app.api.ws_manager import manager
from app.config import settings
from app.services.job_queue import heavy_job_queue

logger = logging.getLogger(__name__)

SHUTDOWN_WS_CODE = 1001
SHUTDOWN_WS_REASON = "server_shutdown"


async def graceful_shutdown() -> None:
    """Вызывается из lifespan при остановке приложения."""
    logger.info("Graceful shutdown: начало")

    for cid in list(manager.busy_conversation_ids()):
        manager.cancel_turn(cid)

    await manager.close_all(
        code=SHUTDOWN_WS_CODE,
        reason=SHUTDOWN_WS_REASON,
        notify={
            "type": "error",
            "message": "Сервер перезапускается. Подключение восстановится после старта.",
            "code": "shutdown",
            "retryable": True,
        },
    )

    heavy_job_queue.begin_shutdown()
    await heavy_job_queue.stop(drain_timeout=settings.shutdown_drain_sec)

    logging.shutdown()
    logger.info("Graceful shutdown: завершено")
