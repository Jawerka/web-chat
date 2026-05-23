"""Доступ к беседе с учётом владельца (P2.2)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation
from app.db.repositories import ConversationRepository
from app.services.request_user import RequestUser, owner_user_id_for_request


async def get_accessible_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user: RequestUser | None,
) -> Conversation | None:
    """Беседа по id, если доступна текущему пользователю."""
    return await ConversationRepository(db).get_by_id_for_owner(
        conversation_id,
        owner_user_id=owner_user_id_for_request(user),
    )
