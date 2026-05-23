"""
Квоты пользователя (P2.2), только при ``MULTI_USER_ENABLED``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import AttachmentRepository, ConversationRepository
from app.errors import ErrorCode
from app.services.request_user import RequestUser


def _quota_http(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": ErrorCode.QUOTA_EXCEEDED, "message": detail},
    )


async def ensure_can_create_conversation(
    db: AsyncSession,
    user: RequestUser | None,
) -> None:
    """Проверить лимит бесед перед созданием."""
    if not settings.effective_multi_user or user is None:
        return
    limit = settings.multi_user_max_conversations
    if limit <= 0:
        return
    count = await ConversationRepository(db).count_by_owner(user.id)
    if count >= limit:
        raise _quota_http(
            f"Достигнут лимит бесед ({limit}). Удалите старые или обратитесь к администратору.",
        )


async def ensure_can_upload(
    db: AsyncSession,
    user: RequestUser | None,
    *,
    new_files: int,
) -> None:
    """Проверить дневной лимит upload для пользователя."""
    if not settings.effective_multi_user or user is None:
        return
    limit = settings.multi_user_max_uploads_per_day
    if limit <= 0 or new_files <= 0:
        return
    since = datetime.now(UTC) - timedelta(days=1)
    count = await AttachmentRepository(db).count_uploads_for_owner(user.id, since=since)
    if count + new_files > limit:
        raise _quota_http(
            f"Достигнут дневной лимит загрузок ({limit}). Повторите позже.",
        )
