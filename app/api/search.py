"""
Поиск по названиям бесед и тексту сообщений.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import MessageSearchHit
from app.db.repositories import ConversationRepository, MessageRepository
from app.db.session import get_db
from app.services.search_snippet import build_search_snippet, search_tokens

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=list[MessageSearchHit])
async def search_messages(
    q: str = Query(..., min_length=1, max_length=200, description="Строка поиска"),
    conversation_id: uuid.UUID | None = Query(
        None,
        description="Ограничить одной беседой",
    ),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[MessageSearchHit]:
    """Найти беседы и сообщения: совпадение любого слова из запроса."""
    words = search_tokens(q)
    if not words:
        return []

    msg_repo = MessageRepository(db)
    conv_repo = ConversationRepository(db)

    rows = await msg_repo.search_in_content(
        words,
        conversation_id=conversation_id,
        limit=limit,
    )
    hits: list[MessageSearchHit] = [
        MessageSearchHit(
            message_id=message.id,
            conversation_id=conversation.id,
            conversation_title=conversation.title,
            role=message.role.value,
            snippet=build_search_snippet(message.content_text or "", q),
            created_at=message.created_at,
            match_kind="message",
        )
        for message, conversation in rows
    ]

    if conversation_id is not None:
        return hits

    seen_convs = {h.conversation_id for h in hits}
    title_limit = max(0, limit - len(hits))
    if title_limit:
        for conv in await conv_repo.search_by_title_words(words, limit=title_limit):
            if conv.id in seen_convs:
                continue
            hits.append(
                MessageSearchHit(
                    message_id=None,
                    conversation_id=conv.id,
                    conversation_title=conv.title,
                    role="",
                    snippet=build_search_snippet(conv.title, q),
                    created_at=conv.updated_at,
                    match_kind="title",
                )
            )
            seen_convs.add(conv.id)
            if len(hits) >= limit:
                break

    return hits[:limit]
