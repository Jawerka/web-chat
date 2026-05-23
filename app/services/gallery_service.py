"""
Список изображений для REST и страницы /gallery: БД (MediaAsset) + локальные файлы.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.db.repositories import GalleryAssetMeta, MediaAssetRepository, MessageRepository
from app.integrations.media_utils import asset_media_url, generated_media_url
from app.services.message_builder import strip_markdown_images

logger = logging.getLogger(__name__)
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

# Максимум карточек в UI галереи (/api/gallery, /gallery).
GALLERY_MAX_LIMIT = 1000


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


def _item_from_gallery_meta(meta: GalleryAssetMeta) -> GalleryItem:
    """Элемент галереи из метаданных MediaAsset (без BLOB)."""
    name = meta.original_name or f"{meta.id}.png"
    url = asset_media_url(meta.id)
    thumb = f"/media/asset/{meta.id}/thumb" if meta.has_thumb else url
    return GalleryItem(
        id=str(meta.id),
        filename=name,
        url=url,
        thumb_url=thumb,
        size_kb=round(meta.size_bytes / 1024, 1),
        mtime=meta.created_at.timestamp(),
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
        thumb = generated_media_url(path.name)
        for ext in (".webp", ".jpg"):
            thumb_name = path.stem + ext
            thumb_path = GENERATED_THUMB_ROOT / thumb_name
            if thumb_path.is_file():
                thumb = generated_thumb_url(thumb_name)
                break
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
    limit: int = GALLERY_MAX_LIMIT,
) -> list[GalleryItem]:
    """
    Объединённая галерея: MediaAsset в SQLite + оставшиеся файлы на диске.

    Дедупликация: если в БД есть asset с original_name как у локального файла,
    показываем только запись из БД.
    """
    limit = max(1, min(GALLERY_MAX_LIMIT, int(limit)))
    repo = MediaAssetRepository(session)
    db_assets = await repo.list_gallery_metadata(limit=limit * 2)

    db_items = [_item_from_gallery_meta(a) for a in db_assets if is_image_mime(a.mime_type)]
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


def _url_variants_for_asset(asset_id: uuid.UUID) -> set[str]:
    """Все варианты URL одного MediaAsset для поиска в сообщениях."""
    path = asset_media_url(asset_id)
    aid = str(asset_id)
    variants = {
        path,
        f"/media/asset/{aid}",
        f"/media/asset/{aid}/thumb",
        f"/media/asset/{aid}/preview",
        f"/media/asset/{aid}/llm",
        aid,
        aid.replace("-", ""),
    }
    return variants


def _url_variants_for_generated(filename: str) -> set[str]:
    safe = filename
    path = generated_media_url(safe)
    stem = Path(safe).stem
    variants = {
        path,
        f"/media/generated/{safe}",
        f"/media/generated/thumbs/{stem}.webp",
        f"/media/generated/thumbs/{stem}.jpg",
        safe,
        stem,
    }
    return variants


def _strip_urls_from_message(
    message: Message,
    needles: set[str],
) -> bool:
    """Убрать ссылки на удалённое изображение из content_json и текста."""
    changed = False
    cj: dict[str, Any] = dict(message.content_json) if isinstance(message.content_json, dict) else {}

    def _matches(value: str) -> bool:
        if not value:
            return False
        return any(n in value for n in needles)

    images = list(cj.get("images") or [])
    new_images = [u for u in images if not _matches(str(u))]
    if new_images != images:
        cj["images"] = new_images
        changed = True

    asset_ids = list(cj.get("image_asset_ids") or [])
    new_aids = [a for a in asset_ids if not _matches(str(a))]
    if new_aids != asset_ids:
        cj["image_asset_ids"] = new_aids
        changed = True

    parts = cj.get("parts")
    if isinstance(parts, list):
        new_parts = []
        for part in parts:
            p = dict(part)
            if p.get("type") == "image_url":
                url = (p.get("image_url") or {}).get("url", "")
                if _matches(str(url)) or _matches(str(p.get("asset_id", ""))):
                    changed = True
                    continue
            new_parts.append(p)
        if changed:
            cj["parts"] = new_parts

    new_text = message.content_text or ""
    if new_text and _matches(new_text):
        for needle in sorted(needles, key=len, reverse=True):
            new_text = new_text.replace(needle, "")
        new_text = strip_markdown_images(new_text)
        changed = True

    if changed:
        message.content_text = new_text
        message.content_json = cj
    return changed


async def purge_asset_from_messages(session: AsyncSession, asset_id: uuid.UUID) -> int:
    """Удалить упоминания asset из всех сообщений."""
    needles = _url_variants_for_asset(asset_id)
    return await _purge_messages_by_needles(session, needles)


async def purge_generated_from_messages(session: AsyncSession, filename: str) -> int:
    """Удалить упоминания generated-файла из всех сообщений."""
    needles = _url_variants_for_generated(filename)
    return await _purge_messages_by_needles(session, needles)


async def _purge_messages_by_needles(session: AsyncSession, needles: set[str]) -> int:
    if not needles:
        return 0
    msg_repo = MessageRepository(session)
    updated = 0
    fragment = max(needles, key=len)[:80]
    candidates = await msg_repo.find_messages_containing(fragment, limit=500)
    for message in candidates:
        if _strip_urls_from_message(message, needles):
            await msg_repo.update_content(
                message,
                content_text=message.content_text or "",
                content_json=message.content_json,
            )
            updated += 1
    if updated:
        logger.info("Очищены ссылки на медиа в %d сообщении(ях)", updated)
    return updated


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
    stem = Path(safe).stem
    for ext in (".webp", ".jpg"):
        (GENERATED_THUMB_ROOT / f"{stem}{ext}").unlink(missing_ok=True)
