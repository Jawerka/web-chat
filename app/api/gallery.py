"""Галерея сгенерированных изображений."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.gallery_service import (
    delete_gallery_asset,
    delete_gallery_disk_file,
    list_gallery_images,
)

_ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

router = APIRouter(tags=["gallery"])


@router.get("/api/gallery")
async def api_gallery(
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """JSON-список изображений галереи (БД + локальные файлы)."""
    items = await list_gallery_images(db, limit=limit)
    return {
        "images": [i.to_api_dict() for i in items],
        "count": len(items),
    }


@router.delete("/api/gallery/db/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_gallery_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить изображение из БД."""
    try:
        await delete_gallery_asset(db, asset_id)
        await db.commit()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено") from exc


@router.delete("/api/gallery/disk/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_gallery_disk(filename: str) -> None:
    """Удалить локальный файл из data/generated/."""
    try:
        delete_gallery_disk_file(filename)
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
