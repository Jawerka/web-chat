"""
REST API быстрых промптов (@alias).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    PromptMacroCreate,
    PromptMacroOut,
    PromptMacroReindexOut,
    PromptMacroSearchHit,
    PromptMacroUpdate,
)
from app.config import settings
from app.db.models import PromptMacroCategory
from app.db.repositories import PromptMacroRepository
from app.db.session import get_db
from app.services.macro_search_service import (
    reindex_all_macro_embeddings,
    refresh_macro_embedding,
    search_macros,
)
from app.services.prompt_macro_service import CATEGORY_LABELS, validate_alias

router = APIRouter(prefix="/prompt-macros", tags=["prompt-macros"])


def _macro_out(m) -> PromptMacroOut:
    return PromptMacroOut(
        id=m.id,
        category=m.category.value,
        category_label=CATEGORY_LABELS.get(m.category, m.category.value),
        alias=m.alias,
        label=m.label,
        body=m.body,
        sort_order=m.sort_order,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get("/categories")
async def list_categories() -> list[dict[str, str]]:
    """Список категорий для UI."""
    return [
        {"id": cat.value, "label": CATEGORY_LABELS[cat]}
        for cat in PromptMacroCategory
    ]


@router.get("/search", response_model=list[PromptMacroSearchHit])
async def search_macro_catalog(
    q: str = Query(..., min_length=1, max_length=2000),
    limit: int | None = Query(None, ge=1, le=50),
    category: PromptMacroCategory | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[PromptMacroSearchHit]:
    """Semantic/keyword поиск по каталогу @alias (Ф2)."""
    cap = limit or settings.macro_search_top_k
    hits = await search_macros(db, q, limit=cap, category=category)
    return [
        PromptMacroSearchHit(
            id=h["macro"].id,
            alias=h["macro"].alias,
            label=h["macro"].label,
            category=h["macro"].category.value,
            score=float(h["score"]),
            match=str(h["match"]),
        )
        for h in hits
    ]


@router.post("/reindex-embeddings", response_model=PromptMacroReindexOut)
async def reindex_macro_embeddings(
    db: AsyncSession = Depends(get_db),
) -> PromptMacroReindexOut:
    """Offline: пересчитать embeddings для всех макросов (не в hot path WS)."""
    stats = await reindex_all_macro_embeddings(db)
    return PromptMacroReindexOut(**stats)


@router.get("", response_model=list[PromptMacroOut])
async def list_macros(
    category: PromptMacroCategory | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[PromptMacroOut]:
    repo = PromptMacroRepository(db)
    macros = await repo.list_all(category=category)
    return [_macro_out(m) for m in macros]


@router.post("", response_model=PromptMacroOut, status_code=status.HTTP_201_CREATED)
async def create_macro(
    body: PromptMacroCreate,
    db: AsyncSession = Depends(get_db),
) -> PromptMacroOut:
    try:
        alias = validate_alias(body.alias)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    repo = PromptMacroRepository(db)
    if await repo.get_by_alias(alias):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Alias @{alias} уже существует",
        )
    try:
        macro = await repo.create(
            category=body.category,
            alias=alias,
            body=body.body.strip(),
            label=body.label.strip() if body.label else None,
            sort_order=body.sort_order,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Alias уже занят",
        ) from exc
    await refresh_macro_embedding(db, macro)
    return _macro_out(macro)


@router.patch("/{macro_id}", response_model=PromptMacroOut)
async def update_macro(
    macro_id: uuid.UUID,
    body: PromptMacroUpdate,
    db: AsyncSession = Depends(get_db),
) -> PromptMacroOut:
    repo = PromptMacroRepository(db)
    macro = await repo.get_by_id(macro_id)
    if macro is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено")

    alias = None
    if body.alias is not None:
        try:
            alias = validate_alias(body.alias)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        existing = await repo.get_by_alias(alias)
        if existing is not None and existing.id != macro.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Alias @{alias} уже существует",
            )

    if body.body is not None and not body.body.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Текст пустой")

    label = macro.label
    if body.label is not None:
        label = body.label.strip() or None

    try:
        macro = await repo.update(
            macro,
            category=body.category,
            alias=alias,
            body=body.body.strip() if body.body is not None else None,
            label=label,
            sort_order=body.sort_order,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Alias уже занят") from exc
    await refresh_macro_embedding(db, macro)
    return _macro_out(macro)


@router.delete("/{macro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_macro(
    macro_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    repo = PromptMacroRepository(db)
    macro = await repo.get_by_id(macro_id)
    if macro is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено")
    await repo.delete(macro)
