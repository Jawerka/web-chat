"""
Поиск быстрых промптов (@alias): keyword + опционально embeddings (Ф2).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import PromptMacro
from app.db.repositories import PromptMacroRepository
from app.integrations.embedding_client import EmbeddingClient, cosine_similarity
from app.services.prompt_macro_service import (
    build_full_macro_catalog_block,
    append_full_macro_catalog_to_system,
)

_TOKEN_RE = re.compile(r"[\w\u0400-\u04ff]+", re.UNICODE)


def _macro_search_document(macro: PromptMacro) -> str:
    parts = [macro.alias, macro.label or "", macro.body]
    return " ".join(p for p in parts if p).strip()


def _keyword_score(query: str, macro: PromptMacro) -> float:
    """Простой скоринг по токенам (fallback без embeddings)."""
    q_tokens = [t.lower() for t in _TOKEN_RE.findall(query) if len(t) > 1]
    if not q_tokens:
        return 0.0
    doc = _macro_search_document(macro).lower()
    alias = macro.alias.lower()
    score = 0.0
    for tok in q_tokens:
        if tok == alias:
            score += 3.0
        elif tok in alias:
            score += 2.0
        elif tok in doc:
            score += 1.0
    return score


async def ensure_macro_embedding(
    macro: PromptMacro,
    *,
    client: EmbeddingClient | None = None,
) -> list[float] | None:
    """Посчитать и вернуть embedding для макроса (без записи в БД)."""
    emb = client or EmbeddingClient()
    return await emb.embed_text(_macro_search_document(macro))


async def search_macros(
    session: AsyncSession,
    query: str,
    *,
    limit: int | None = None,
    category=None,
) -> list[dict[str, Any]]:
    """
    Top-K макросов по запросу: semantic (если есть векторы) иначе keyword.

    Returns:
        Список dict с полями macro, score, match (semantic|keyword).
    """
    repo = PromptMacroRepository(session)
    macros = await repo.list_all(category=category)
    if not macros:
        return []

    cap = limit or settings.macro_search_top_k
    q = query.strip()
    if not q:
        return [
            {
                "macro": m,
                "score": 0.0,
                "match": "list",
            }
            for m in macros[:cap]
        ]

    client = EmbeddingClient()
    q_vec = await client.embed_text(q)
    semantic_hits: list[tuple[float, PromptMacro]] = []
    if q_vec:
        for macro in macros:
            vec = macro.embedding_json if isinstance(macro.embedding_json, list) else None
            if vec and len(vec) == len(q_vec):
                semantic_hits.append((cosine_similarity(q_vec, vec), macro))
        semantic_hits.sort(key=lambda x: x[0], reverse=True)
        if semantic_hits and semantic_hits[0][0] > 0.05:
            return [
                {
                    "macro": m,
                    "score": round(s, 4),
                    "match": "semantic",
                }
                for s, m in semantic_hits[:cap]
            ]

    kw_scored = [(_keyword_score(q, m), m) for m in macros]
    kw_scored.sort(key=lambda x: x[0], reverse=True)
    top = kw_scored[:cap]
    if top and top[0][0] == 0.0:
        top = [(0.0, m) for m in macros[:cap]]
    return [
        {"macro": m, "score": round(s, 2), "match": "keyword"} for s, m in top
    ]


async def build_semantic_macro_catalog_for_query(
    session: AsyncSession,
    query: str,
    *,
    limit: int | None = None,
    max_chars: int | None = None,
) -> str:
    """Снимок Top-K макросов, релевантных запросу пользователя (macro_context=semantic)."""
    hits = await search_macros(session, query, limit=limit)
    macros = [h["macro"] for h in hits]
    return build_full_macro_catalog_block(
        macros,
        max_chars=max_chars or settings.macro_context_full_max_chars,
        max_macros=limit or settings.macro_search_top_k,
    )


async def apply_macro_context_to_system(
    session: AsyncSession,
    system_prompt: str,
    macro_context: str,
    *,
    user_text: str = "",
    all_macros: list[PromptMacro] | None = None,
) -> str:
    """Дополнить system prompt каталогом (full) или top-K (semantic)."""
    if macro_context == "full":
        macros = all_macros
        if macros is None:
            macros = await PromptMacroRepository(session).list_all()
        return append_full_macro_catalog_to_system(
            system_prompt,
            macros,
            max_chars=settings.macro_context_full_max_chars,
            max_macros=settings.macro_context_full_max_macros,
        )
    if macro_context == "semantic" and user_text.strip():
        block = await build_semantic_macro_catalog_for_query(session, user_text)
        if block:
            base = system_prompt.rstrip()
            return f"{base}\n\n{block}" if base else block
    return system_prompt


async def refresh_macro_embedding(session: AsyncSession, macro: PromptMacro) -> bool:
    """Пересчитать embedding одного макроса (best-effort)."""
    vec = await ensure_macro_embedding(macro)
    macro.embedding_json = vec
    await session.flush()
    return vec is not None


async def reindex_all_macro_embeddings(session: AsyncSession) -> dict[str, int]:
    """Пересчитать embeddings для всех макросов (фоновая/ручная индексация)."""
    repo = PromptMacroRepository(session)
    macros = await repo.list_all()
    client = EmbeddingClient()
    updated = 0
    skipped = 0
    for macro in macros:
        vec = await ensure_macro_embedding(macro, client=client)
        if vec is None:
            macro.embedding_json = None
            skipped += 1
        else:
            macro.embedding_json = vec
            updated += 1
    await session.flush()
    return {"updated": updated, "skipped": skipped, "total": len(macros)}
