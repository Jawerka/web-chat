"""
REST: индексация и поиск по документам беседы (P2.3).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services.conversation_access import get_accessible_conversation
from app.services.document_rag_service import index_attachment_rag, search_conversation_documents
from app.services.request_user import RequestUser, get_request_user

router = APIRouter(tags=["document-rag"])


class DocumentSearchHit(BaseModel):
    chunk_id: str
    attachment_id: str
    file_name: str
    chunk_index: int
    score: float
    snippet: str


class RagIndexOut(BaseModel):
    chunks: int
    embedded: int = 0
    skipped: int = 0
    reason: str | None = None


@router.post(
    "/attachments/{attachment_id}/index-rag",
    response_model=RagIndexOut,
)
async def index_attachment(
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> RagIndexOut:
    """Проиндексировать extracted_text вложения для semantic search."""
    if not settings.rag_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG отключён (RAG_ENABLED=false)",
        )
    from app.db.repositories import AttachmentRepository

    att = await AttachmentRepository(db).get_by_id(attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    if att.conversation_id is not None:
        if await get_accessible_conversation(db, att.conversation_id, user) is None:
            raise HTTPException(status_code=404, detail="Беседа не найдена")

    try:
        stats = await index_attachment_rag(db, attachment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return RagIndexOut(
        chunks=stats.get("chunks", 0),
        embedded=stats.get("embedded", 0),
        skipped=stats.get("skipped", 0),
        reason=stats.get("reason"),
    )


@router.get(
    "/conversations/{conversation_id}/document-search",
    response_model=list[DocumentSearchHit],
)
async def search_documents(
    conversation_id: uuid.UUID,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> list[DocumentSearchHit]:
    """Semantic/keyword поиск по документам беседы."""
    if not settings.rag_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG отключён",
        )
    if await get_accessible_conversation(db, conversation_id, user) is None:
        raise HTTPException(status_code=404, detail="Беседа не найдена")

    hits = await search_conversation_documents(db, conversation_id, q, limit=limit)
    return [DocumentSearchHit(**h) for h in hits]
