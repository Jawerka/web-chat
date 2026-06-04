"""Галерея сгенерированных изображений."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_events import broadcast_gallery_update
from app.db.repositories import MediaFavoriteRepository
from app.db.session import get_db
from app.services.gallery_owner import require_gallery_owner_user, resolve_gallery_owner_id
from app.services.gallery_service import (
    GALLERY_MAX_LIMIT,
    cleanup_gallery_orphans,
    delete_gallery_asset,
    delete_gallery_disk_file,
    list_gallery_images,
    purge_all_gallery,
)
from app.services.request_user import RequestUser, get_request_user

_ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

router = APIRouter(tags=["gallery"])


class GalleryFavoritePayload(BaseModel):
    source: str = Field(min_length=2, max_length=16)
    id: str = Field(min_length=1, max_length=255)
    favorite: bool = True


@router.get("/api/gallery")
async def api_gallery(
    limit: int = GALLERY_MAX_LIMIT,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    """JSON-список изображений галереи (БД + локальные файлы)."""
    items = await list_gallery_images(db, limit=limit, request_user=user)
    return {
        "images": [i.to_api_dict() for i in items],
        "count": len(items),
    }


@router.post("/api/gallery/favorite")
async def api_gallery_favorite(
    payload: GalleryFavoritePayload,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    source = payload.source.strip().lower()
    if source not in {"db", "disk"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source должен быть db или disk")
    media_id = payload.id.strip()
    owner = await require_gallery_owner_user(db, user)
    repo = MediaFavoriteRepository(db)
    is_favorite = await repo.set_favorite(
        source=source,
        media_id=media_id,
        is_favorite=bool(payload.favorite),
        user_id=owner.id,
    )
    await db.commit()
    await broadcast_gallery_update(
        "favorite",
        source=source,
        id=media_id,
        favorite=is_favorite,
        user_id=owner.id,
    )
    return {"ok": True, "source": source, "id": media_id, "is_favorite": is_favorite}


@router.get("/api/gallery/favorite/state")
async def api_gallery_favorite_state(
    source: str,
    id: str,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    source_norm = source.strip().lower()
    if source_norm not in {"db", "disk"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source должен быть db или disk")
    media_id = id.strip()
    owner_id = await resolve_gallery_owner_id(db, user)
    repo = MediaFavoriteRepository(db)
    return {
        "source": source_norm,
        "id": media_id,
        "is_favorite": await repo.is_favorite(
            source=source_norm,
            media_id=media_id,
            user_id=owner_id,
        ),
    }


@router.post("/api/gallery/cleanup-orphans")
async def api_cleanup_orphan_generated(
    dry_run: bool = False,
    min_age_hours: float | None = None,
    purge_messages: bool = False,
    dedup_db: bool = True,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Orphan на диске (``data/generated/``) и в MediaAsset (без ссылок + dedup).

    ``dry_run=true`` — только списки кандидатов.
    """
    stats = await cleanup_gallery_orphans(
        db,
        dry_run=dry_run,
        min_age_hours=min_age_hours,
        purge_messages=purge_messages,
        dedup_db=dedup_db,
    )
    if not dry_run:
        removed = int(stats.get("disk", {}).get("deleted", 0)) + int(
            stats.get("db", {}).get("deleted", 0),
        )
        if removed > 0:
            await db.commit()
            await broadcast_gallery_update("cleanup_orphans", count=removed)
    return stats


@router.delete("/api/gallery/all")
async def api_purge_gallery_all(
    purge_messages: bool = True,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить все изображения галереи (с подтверждением на клиенте)."""
    stats = await purge_all_gallery(db, purge_messages=purge_messages)
    await db.commit()
    await broadcast_gallery_update("purge_all", count=stats.get("deleted_db", 0) + stats.get("deleted_disk", 0))
    return stats


@router.delete("/api/gallery/db/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_gallery_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить изображение из БД."""
    try:
        from app.services.gallery_service import purge_asset_from_messages

        await delete_gallery_asset(db, asset_id)
        await purge_asset_from_messages(db, asset_id)
        await db.commit()
        await broadcast_gallery_update("deleted", asset_id=str(asset_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено") from exc


@router.delete("/api/gallery/disk/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_gallery_disk(
    filename: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить локальный файл из data/generated/."""
    try:
        delete_gallery_disk_file(filename)
        from app.services.gallery_service import purge_generated_from_messages

        await purge_generated_from_messages(db, filename)
        await db.commit()
        await broadcast_gallery_update("deleted")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/gallery", response_class=HTMLResponse, include_in_schema=False)
async def gallery_page(request: Request) -> HTMLResponse:
    """Страница галереи (данные подгружаются через /api/gallery)."""
    return templates.TemplateResponse(
        request,
        "gallery.html",
        {"title": "Галерея"},
    )
