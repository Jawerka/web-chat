"""
Корзина бесед: окончательное удаление по сроку хранения.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import ConversationRepository

logger = logging.getLogger(__name__)


async def purge_expired_trash(session: AsyncSession) -> int:
    """Удалить из БД беседы в корзине старше trash_retention_days."""
    days = max(1, settings.trash_retention_days)
    repo = ConversationRepository(session)
    removed = await repo.purge_trash_older_than(days=days)
    if removed:
        logger.info(
            "Корзина: окончательно удалено %d бесед (старше %d дн.)",
            removed,
            days,
        )
    return removed
