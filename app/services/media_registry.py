"""
Единый media registry: метаданные в БД — источник правды (P1.5).

Файлы на диске ``data/generated/`` — промежуточное хранилище до ingest;
после регистрации asset обслуживается через ``/media/asset/{id}``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MediaAsset
from app.db.repositories import GalleryAssetMeta, MediaAssetRepository
from app.db.uow import SqlAlchemyUnitOfWork
from app.integrations.media_utils import (
    GENERATED_ROOT,
    asset_media_url,
    is_image_mime,
    make_asset_thumb_bytes,
    safe_filename,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegisteredAsset:
    """Зарегистрированный в БД asset с публичным URL."""

    asset: MediaAsset
    url: str

    @property
    def id(self) -> uuid.UUID:
        return self.asset.id


class MediaRegistry:
    """DB-first реестр изображений (галерея, SD output, upload→asset)."""

    def __init__(self, session: AsyncSession) -> None:
        self._uow = SqlAlchemyUnitOfWork(session)
        self._repo = self._uow.media_assets

    async def register_image(
        self,
        data: bytes,
        mime_type: str,
        *,
        conversation_id: uuid.UUID | None = None,
        original_name: str | None = None,
        thumb_data: bytes | None = None,
    ) -> RegisteredAsset:
        """Создать MediaAsset в БД и вернуть URL для UI/LLM."""
        thumb = thumb_data
        if thumb is None and is_image_mime(mime_type):
            thumb = make_asset_thumb_bytes(data)
        asset = await self._repo.create(
            data=data,
            mime_type=mime_type,
            conversation_id=conversation_id,
            original_name=original_name,
            thumb_data=thumb,
        )
        url = asset_media_url(asset.id)
        logger.info("media_registry: зарегистрирован %s (%s)", asset.id, original_name or mime_type)
        return RegisteredAsset(asset=asset, url=url)

    async def register_batch(
        self,
        items: list[tuple[bytes, str, uuid.UUID | None, str | None]],
    ) -> list[RegisteredAsset]:
        """Пакетная регистрация (меньше lock-конфликтов SQLite при SD ingest)."""
        out: list[RegisteredAsset] = []
        for data, mime_type, conversation_id, original_name in items:
            out.append(
                await self.register_image(
                    data,
                    mime_type,
                    conversation_id=conversation_id,
                    original_name=original_name,
                )
            )
        return out

    async def register_from_generated_file(
        self,
        path: Path,
        *,
        conversation_id: uuid.UUID | None = None,
        mime_type: str | None = None,
    ) -> RegisteredAsset | None:
        """Перенести файл из data/generated/ в БД; удалить файл с диска при успехе."""
        if not path.is_file():
            return None
        filename = safe_filename(path.name)
        data = path.read_bytes()
        guessed = mime_type or _guess_mime(filename)
        reg = await self.register_image(
            data,
            guessed,
            conversation_id=conversation_id,
            original_name=filename,
        )
        try:
            path.unlink(missing_ok=True)
            stem = Path(filename).stem
            for ext in (".webp", ".jpg"):
                (GENERATED_ROOT / "thumbs" / f"{stem}{ext}").unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("media_registry: не удалось удалить %s: %s", path, exc)
        return reg

    async def list_gallery_metadata(self, limit: int = 1000) -> list[GalleryAssetMeta]:
        """Метаданные изображений из БД (без BLOB)."""
        return await self._repo.list_gallery_metadata(limit=limit)

    async def get_by_id(self, asset_id: uuid.UUID) -> MediaAsset | None:
        return await self._repo.get_by_id(asset_id)

    async def delete_asset(self, asset_id: uuid.UUID) -> None:
        """Удалить asset из БД."""
        asset = await self._repo.get_by_id(asset_id)
        if asset is None:
            raise FileNotFoundError(str(asset_id))
        await self._repo.delete(asset)
        logger.info("media_registry: удалён %s", asset_id)

    @staticmethod
    def disk_filename_claimed_by_db(
        filename: str,
        db_original_names: set[str],
    ) -> bool:
        """Файл на диске уже представлен записью БД (по original_name)."""
        return filename.lower() in db_original_names


def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/png")
