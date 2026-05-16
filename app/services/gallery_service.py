"""
Список изображений для REST и страницы /gallery: БД (MediaAsset) + локальные файлы.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import MediaAsset
from app.db.repositories import MediaAssetRepository
from app.integrations.media_utils import (
    GENERATED_ROOT,
    GENERATED_THUMB_ROOT,
    asset_media_url,
    generated_media_url,
    generated_thumb_url,
    is_image_mime,
    resolve_generated_file,
    safe_filename,
)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@dataclass(frozen=True, slots=True)
class GalleryItem:
    """Один элемент галереи."""

    id: str
    filename: str
    url: str
    thumb_url: str
    size_kb: float
    mtime: float
    source: str = "disk"  # "db" | "disk"

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "url": self.url,
            "thumb_url": self.thumb_url,
            "size_kb": self.size_kb,
            "mtime": self.mtime,
            "source": self.source,
        }


def _item_from_asset(asset: MediaAsset) -> GalleryItem:
    """Элемент галереи из записи MediaAsset."""
    name = asset.original_name or f"{asset.id}.png"
    url = asset_media_url(asset.id, absolute=True)
    thumb = (
        f"{settings.public_base_url.rstrip('/')}/media/asset/{asset.id}/thumb"
        if asset.thumb_data
        else url
    )
    return GalleryItem(
        id=str(asset.id),
        filename=name,
        url=url,
        thumb_url=thumb,
        size_kb=round(len(asset.data) / 1024, 1),
        mtime=asset.created_at.timestamp(),
        source="db",
    )


def _list_local_generated_images(limit: int) -> list[GalleryItem]:
    """Файлы в data/generated/, ещё не перенесённые в БД."""
    if not GENERATED_ROOT.is_dir():
        return []

    paths = [
        p for p in GENERATED_ROOT.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    ]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    items: list[GalleryItem] = []
    for path in paths[:limit]:
        thumb_name = path.stem + ".jpg"
        thumb_path = GENERATED_THUMB_ROOT / thumb_name
        thumb = (
            generated_thumb_url(thumb_name)
            if thumb_path.is_file()
            else generated_media_url(path.name)
        )
        stat = path.stat()
        items.append(
            GalleryItem(
                id=path.name,
                filename=path.name,
                url=generated_media_url(path.name),
                thumb_url=thumb,
                size_kb=round(stat.st_size / 1024, 1),
                mtime=stat.st_mtime,
                source="disk",
            )
        )
    return items


async def list_gallery_images(
    session: AsyncSession,
    limit: int = 80,
) -> list[GalleryItem]:
    """
    Объединённая галерея: MediaAsset в SQLite + оставшиеся файлы на диске.

    Дедупликация: если в БД есть asset с original_name как у локального файла,
    показываем только запись из БД.
    """
    limit = max(1, min(500, int(limit)))
    repo = MediaAssetRepository(session)
    db_assets = await repo.list_images_recent(limit=limit * 2)

    db_items = [_item_from_asset(a) for a in db_assets if is_image_mime(a.mime_type)]
    ingested_names = {(a.original_name or "").lower() for a in db_assets if a.original_name}

    local_items: list[GalleryItem] = []
    for item in _list_local_generated_images(limit=limit * 2):
        if item.filename.lower() in ingested_names:
            continue
        local_items.append(item)

    merged = db_items + local_items
    merged.sort(key=lambda x: x.mtime, reverse=True)
    return merged[:limit]


def list_generated_images(limit: int = 50) -> list[GalleryItem]:
    """Только локальные файлы (для sync/MCP без сессии БД)."""
    return _list_local_generated_images(limit=limit)


async def delete_gallery_asset(session: AsyncSession, asset_id: uuid.UUID) -> None:
    """Удалить изображение из БД."""
    repo = MediaAssetRepository(session)
    asset = await repo.get_by_id(asset_id)
    if asset is None:
        raise FileNotFoundError(str(asset_id))
    await repo.delete(asset)


def delete_gallery_disk_file(filename: str) -> None:
    """Удалить файл из data/generated/ и миниатюру."""
    safe = safe_filename(filename)
    if not safe:
        raise ValueError("Недопустимое имя файла")
    path = resolve_generated_file(safe, thumbs=False)
    path.unlink(missing_ok=True)
    thumb = GENERATED_THUMB_ROOT / f"{Path(safe).stem}.jpg"
    thumb.unlink(missing_ok=True)
