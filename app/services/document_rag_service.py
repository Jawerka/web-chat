"""
RAG по extracted_text вложений беседы (P2.3).

Индексация offline/после extract; поиск — semantic (+ keyword fallback).
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Attachment, DocumentChunk
from app.db.repositories import AttachmentRepository, DocumentChunkRepository
from app.integrations.embedding_client import EmbeddingClient, cosine_similarity

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{2,}", re.IGNORECASE)


def split_text_into_chunks(
    text: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """Разбить текст на перекрывающиеся фрагменты."""
    raw = (text or "").strip()
    if not raw:
        return []
    size = chunk_size if chunk_size is not None else settings.rag_chunk_chars
    ov = overlap if overlap is not None else settings.rag_chunk_overlap
    size = max(200, size)
    ov = min(ov, size // 2)
    if len(raw) <= size:
        return [raw]
    chunks: list[str] = []
    start = 0
    while start < len(raw):
        piece = raw[start : start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(raw):
            break
        start = max(0, start + size - ov)
    return chunks


def _keyword_score(query: str, text: str) -> float:
    q_tokens = set(_TOKEN_RE.findall(query.lower()))
    if not q_tokens:
        return 0.0
    t_tokens = set(_TOKEN_RE.findall(text.lower()))
    if not t_tokens:
        return 0.0
    return len(q_tokens & t_tokens) / len(q_tokens)


async def index_attachment_rag(
    session: AsyncSession,
    attachment_id: uuid.UUID,
    *,
    client: EmbeddingClient | None = None,
) -> dict[str, int]:
    """
    Проиндексировать вложение: чанки + embeddings.

    Требует ``extracted_text`` и ``RAG_ENABLED`` + ``EMBEDDING_MODEL``.
    """
    if not settings.rag_enabled:
        return {"chunks": 0, "skipped": 1, "reason": "disabled"}

    att_repo = AttachmentRepository(session)
    attachment = await att_repo.get_by_id(attachment_id)
    if attachment is None:
        raise ValueError(f"Вложение не найдено: {attachment_id}")

    text = (attachment.extracted_text or "").strip()
    if not text:
        return {"chunks": 0, "skipped": 1, "reason": "no_text"}

    pieces = split_text_into_chunks(text)
    if not pieces:
        return {"chunks": 0, "skipped": 1, "reason": "empty"}

    embed_client = client or EmbeddingClient()
    chunk_repo = DocumentChunkRepository(session)
    await chunk_repo.delete_for_attachment(attachment_id)

    rows: list[tuple[int, str, list[float] | None]] = []
    embedded = 0
    for index, piece in enumerate(pieces):
        vec = await embed_client.embed_text(piece)
        if vec is not None:
            embedded += 1
        rows.append((index, piece, vec))

    await chunk_repo.create_chunks(
        attachment_id=attachment_id,
        conversation_id=attachment.conversation_id,
        chunks=rows,
    )
    logger.info(
        "RAG index attachment=%s chunks=%d embedded=%d",
        attachment_id,
        len(rows),
        embedded,
    )
    return {"chunks": len(rows), "embedded": embedded, "skipped": 0}


async def search_conversation_documents(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    query: str,
    *,
    limit: int | None = None,
) -> list[dict]:
    """Top-K фрагментов документов беседы по запросу."""
    q = query.strip()
    if not q:
        return []

    top_k = limit if limit is not None else settings.rag_search_top_k
    rows = await DocumentChunkRepository(session).list_for_conversation(conversation_id)
    if not rows:
        return []

    query_vec: list[float] | None = None
    if settings.embedding_model.strip():
        query_vec = await EmbeddingClient().embed_text(q)

    scored: list[tuple[float, DocumentChunk, Attachment]] = []
    for chunk, attachment in rows:
        if query_vec and isinstance(chunk.embedding_json, list) and chunk.embedding_json:
            score = cosine_similarity(query_vec, chunk.embedding_json)
        else:
            score = _keyword_score(q, chunk.text)
        if score > 0:
            scored.append((score, chunk, attachment))

    scored.sort(key=lambda x: x[0], reverse=True)
    hits: list[dict] = []
    for score, chunk, attachment in scored[:top_k]:
        hits.append(
            {
                "chunk_id": str(chunk.id),
                "attachment_id": str(attachment.id),
                "file_name": attachment.original_name,
                "chunk_index": chunk.chunk_index,
                "score": round(score, 4),
                "snippet": chunk.text[:400],
            },
        )
    return hits


def build_rag_context_block(hits: list[dict], *, max_chars: int | None = None) -> str:
    """Сформировать блок для system prompt из результатов поиска."""
    if not hits:
        return ""
    limit = max_chars if max_chars is not None else settings.rag_context_max_chars
    lines = [
        "Релевантные фрагменты документов беседы (используй только при ответе по их содержанию):",
    ]
    used = len(lines[0])
    for hit in hits:
        line = (
            f"- [{hit['file_name']} #{hit['chunk_index']}] "
            f"{hit['snippet'].replace(chr(10), ' ')}"
        )
        if used + len(line) + 1 > limit:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


async def append_document_rag_to_system(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    user_text: str,
    system_prompt: str,
) -> str:
    """Дополнить system prompt top-K фрагментами документов (``rag_auto_inject``)."""
    if not settings.rag_enabled or not settings.rag_auto_inject:
        return system_prompt
    if not user_text.strip():
        return system_prompt
    hits = await search_conversation_documents(session, conversation_id, user_text)
    block = build_rag_context_block(hits)
    if not block:
        return system_prompt
    base = system_prompt.rstrip()
    return f"{base}\n\n{block}" if base else block


async def maybe_index_attachment_after_extract(
    session: AsyncSession,
    attachment: Attachment,
) -> None:
    """Best-effort индексация после extract_text (не блокирует при ошибке)."""
    if not settings.rag_enabled or not settings.embedding_model.strip():
        return
    try:
        await index_attachment_rag(session, attachment.id)
    except Exception as exc:
        logger.warning("RAG index failed for %s: %s", attachment.id, exc)
