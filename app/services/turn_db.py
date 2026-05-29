"""
Короткоживущие сессии БД на время хода агента (P3.1).

Соединение с пулом не удерживается во время долгих await (LLM, SD).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session


@asynccontextmanager
async def open_turn_session() -> AsyncIterator[AsyncSession]:
    """Открыть сессию на одну транзакционную операцию хода."""
    async with db_session.async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
