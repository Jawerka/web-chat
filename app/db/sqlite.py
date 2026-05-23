"""
Настройки SQLite: WAL, busy_timeout, сериализация записей.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# Одна очередь на запись — избегает database is locked при WS + ingest.
_sqlite_write_lock = asyncio.Lock()
_sqlite_busy_retries_total: int = 0


def sqlite_busy_retries_total() -> int:
    """Сколько раз сработал retry при database is locked (метрика P1.1)."""
    return _sqlite_busy_retries_total


def configure_sqlite_engine(engine: AsyncEngine) -> None:
    """WAL и busy_timeout для каждого нового подключения."""
    if "sqlite" not in str(engine.url):
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn, _record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()

    logger.debug("SQLite: WAL + busy_timeout=60000 для %s", engine.url)


async def run_write(
    factory: async_sessionmaker[AsyncSession],
    callback,
    *,
    attempts: int = 12,
    operation: str = "write",
):
    """
    Выполнить запись в БД с блокировкой и повторами при database is locked.

    callback(session) -> T
    """
    last_err: Exception | None = None
    async with _sqlite_write_lock:
        for attempt in range(attempts):
            try:
                async with factory() as session:
                    result = await callback(session)
                    await session.commit()
                    logger.debug("SQLite OK: %s", operation)
                    return result
            except OperationalError as exc:
                last_err = exc
                msg = str(exc).lower()
                if "locked" not in msg and "busy" not in msg:
                    logger.error("SQLite ошибка [%s]: %s", operation, exc)
                    raise
                if attempt >= attempts - 1:
                    logger.error(
                        "SQLite busy: исчерпаны попытки [%s] (%d): %s",
                        operation,
                        attempts,
                        exc,
                    )
                    raise
                global _sqlite_busy_retries_total
                _sqlite_busy_retries_total += 1
                delay = min(2.0, 0.08 * (2**attempt))
                logger.warning(
                    "SQLite busy [%s] (попытка %d/%d), повтор через %.2fs: %s",
                    operation,
                    attempt + 1,
                    attempts,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
    if last_err:
        raise last_err
    raise RuntimeError(f"run_write failed without result [{operation}]")
