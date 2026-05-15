"""
Хранение и раздача изображений из БД (+ импорт внешних URL).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import MediaAsset
from app.db.repositories import MediaAssetRepository
from app.integrations.media_utils import (
    GENERATED_ROOT,
    UPLOAD_ROOT,
    asset_media_url,
    is_image_mime,
    resolve_generated_file,
    resolve_upload_file,
    safe_filename,
)

logger = logging.getLogger(__name__)

_MAX_IMPORT_BYTES = 15 * 1024 * 1024
_ASSET_URL_RE = re.compile(
    r"/media/asset/([0-9a-fA-F-]{36})(?:/thumb)?",
    re.IGNORECASE,
)
_GENERATED_URL_RE = re.compile(
    r"/media/generated/(?:thumbs/)?([^\s\)?#]+\.(?:png|jpg|jpeg|webp|gif))",
    re.IGNORECASE,
)
_UPLOAD_URL_RE = re.compile(
    r"/media/uploads/([0-9a-fA-F-]{36})/([^/\s\)?#]+)",
    re.IGNORECASE,
)
_MARKDOWN_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


class MediaService:
    """Создание MediaAsset и нормализация URL для чата и LLM."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MediaAssetRepository(session)

    @staticmethod
    def public_url(asset_id: uuid.UUID) -> str:
        return asset_media_url(asset_id)

    @staticmethod
    def thumb_url(asset_id: uuid.UUID) -> str:
        base = settings.public_base_url.rstrip("/")
        return f"{base}/media/asset/{asset_id}/thumb"

    async def create_from_bytes(
        self,
        data: bytes,
        mime_type: str,
        *,
        conversation_id: uuid.UUID | None = None,
        original_name: str | None = None,
        thumb_data: bytes | None = None,
    ) -> MediaAsset:
        """Сохранить изображение в БД (в текущей сессии)."""
        if thumb_data is None and is_image_mime(mime_type):
            thumb_data = await asyncio.to_thread(_make_thumb_bytes, data)
        return await self._repo.create(
            data=data,
            mime_type=mime_type,
            conversation_id=conversation_id,
            original_name=original_name,
            thumb_data=thumb_data,
        )

    @staticmethod
    async def create_from_bytes_committed(
        data: bytes,
        mime_type: str,
        *,
        conversation_id: uuid.UUID | None = None,
        original_name: str | None = None,
        thumb_data: bytes | None = None,
    ) -> MediaAsset:
        """
        Сохранить изображение в отдельной транзакции.

        Переживает rollback основного хода WS (генерация / перегенерация).
        """
        assets = await MediaService.commit_media_assets_batch([(
            data,
            mime_type,
            conversation_id,
            original_name,
            thumb_data,
        )])
        return assets[0]

    @staticmethod
    async def commit_media_assets_batch(
        items: list[tuple[bytes, str, uuid.UUID | None, str | None, bytes | None]],
    ) -> list[MediaAsset]:
        """Сохранить несколько изображений одной транзакцией (меньше lock-конфликтов)."""
        from app.db.session import async_session_factory
        from app.db.sqlite import run_write

        prepared: list[tuple[bytes, str, uuid.UUID | None, str | None, bytes | None]] = []
        for data, mime_type, conversation_id, original_name, thumb_data in items:
            thumb = thumb_data
            if thumb is None and is_image_mime(mime_type):
                thumb = await asyncio.to_thread(_make_thumb_bytes, data)
            prepared.append((data, mime_type, conversation_id, original_name, thumb))

        async def _write(session):
            repo = MediaAssetRepository(session)
            assets: list[MediaAsset] = []
            for data, mime_type, conversation_id, original_name, thumb in prepared:
                assets.append(
                    await repo.create(
                        data=data,
                        mime_type=mime_type,
                        conversation_id=conversation_id,
                        original_name=original_name,
                        thumb_data=thumb,
                    )
                )
            return assets

        n = len(prepared)
        logger.info("commit_media_assets_batch: %d файл(ов)", n)
        return await run_write(
            async_session_factory,
            _write,
            operation=f"media_assets_batch({n})",
        )

    async def get_bytes(self, asset_id: uuid.UUID) -> tuple[bytes, str] | None:
        asset = await self._repo.get_by_id(asset_id)
        if asset is None:
            return None
        return asset.data, asset.mime_type

    async def get_thumb_bytes(self, asset_id: uuid.UUID) -> tuple[bytes, str] | None:
        asset = await self._repo.get_by_id(asset_id)
        if asset is None:
            return None
        if asset.thumb_data:
            return asset.thumb_data, "image/jpeg"
        thumb = await asyncio.to_thread(_make_thumb_bytes, asset.data)
        if thumb:
            return thumb, "image/jpeg"
        return asset.data, asset.mime_type

    async def normalize_image_url(
        self,
        url: str,
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> str:
        """
        Привести URL к локальному /media/asset/{id}.

        Внешние и legacy (/media/generated, /media/uploads) загружаются в БД.
        """
        url = url.strip()
        if not url:
            return url

        asset_match = _ASSET_URL_RE.search(url)
        if asset_match:
            return asset_media_url(uuid.UUID(asset_match.group(1)))

        try:
            if url.startswith("/media/"):
                base = settings.public_base_url.rstrip("/")
                fetch_url = f"{base}{url}"
            elif url.startswith("http://") or url.startswith("https://"):
                fetch_url = url
            else:
                return url

            data, mime = await self._load_image_bytes(fetch_url, url)
            asset = await self.create_from_bytes(
                data,
                mime,
                conversation_id=conversation_id,
                original_name=_name_from_url(url),
            )
            return asset_media_url(asset.id)
        except Exception as exc:
            logger.warning("Не удалось импортировать изображение %s: %s", url[:80], exc)
            return url

    async def normalize_image_urls(
        self,
        urls: list[str],
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> list[str]:
        """Нормализовать список URL (без дубликатов)."""
        out: list[str] = []
        for url in urls:
            normalized = await self.normalize_image_url(
                url,
                conversation_id=conversation_id,
            )
            if normalized and normalized not in out:
                out.append(normalized)
        return out

    async def _load_image_bytes(self, fetch_url: str, original: str) -> tuple[bytes, str]:
        """Прочитать байты: с диска (legacy) или по HTTP."""
        parsed = urlparse(fetch_url)
        path = parsed.path or original

        gen = _GENERATED_URL_RE.search(path)
        if gen:
            filename = safe_filename(gen.group(1))
            thumbs = "/thumbs/" in path
            file_path = resolve_generated_file(filename, thumbs=thumbs)
            data = file_path.read_bytes()
            mime = _guess_mime(file_path.name)
            return data, mime

        upl = _UPLOAD_URL_RE.search(path)
        if upl:
            att_id = uuid.UUID(upl.group(1))
            fname = safe_filename(upl.group(2))
            file_path = resolve_upload_file(att_id, fname)
            data = file_path.read_bytes()
            mime = _guess_mime(file_path.name)
            return data, mime

        return await _fetch_url_bytes(fetch_url)

    async def ingest_sd_output_files(
        self,
        tool_output: str,
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> tuple[list[str], dict[str, str], list[uuid.UUID]]:
        """
        После generate_image: перенести файлы с диска в БД.

        Returns:
            (относительные URL, карта старый→новый URL, id ассетов)
        """
        urls: list[str] = []
        url_map: dict[str, str] = {}
        asset_ids: list[uuid.UUID] = []
        seen_files: set[str] = set()
        pending: list[tuple[bytes, str, uuid.UUID | None, str | None, bytes | None]] = []
        pending_meta: list[tuple[str, Path]] = []

        for match in _GENERATED_URL_RE.finditer(tool_output):
            filename = safe_filename(match.group(1))
            if not filename or filename in seen_files:
                continue
            seen_files.add(filename)
            path = GENERATED_ROOT / filename
            if not path.is_file():
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                logger.warning("ingest SD file %s: %s", filename, exc)
                continue
            pending.append((
                data,
                _guess_mime(filename),
                conversation_id,
                filename,
                None,
            ))
            pending_meta.append((filename, path))

        if pending:
            logger.info(
                "ingest_sd_output_files: %d файл(ов), conv=%s",
                len(pending),
                conversation_id,
            )
            try:
                assets = await MediaService.commit_media_assets_batch(pending)
            except Exception as exc:
                logger.error(
                    "Не удалось сохранить изображения в БД (%d шт.): %s",
                    len(pending),
                    exc,
                    exc_info=True,
                )
                raise
            for asset, (filename, path) in zip(assets, pending_meta, strict=True):
                new_url = asset_media_url(asset.id)
                urls.append(new_url)
                asset_ids.append(asset.id)
                for old in _generated_url_variants(filename):
                    url_map[old] = new_url
                try:
                    path.unlink(missing_ok=True)
                    thumb = GENERATED_ROOT / "thumbs" / f"{Path(filename).stem}.jpg"
                    thumb.unlink(missing_ok=True)
                except OSError:
                    pass
                logger.info(
                    "Изображение в БД: %s → %s",
                    filename,
                    new_url,
                )

        return urls, url_map, asset_ids

    async def ensure_asset_url(
        self,
        url: str,
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> str:
        """
        Гарантировать рабочий /media/asset/ URL (импорт legacy / generated при чтении).
        """
        url = url.strip()
        if not url:
            return url

        asset_match = _ASSET_URL_RE.search(url)
        if asset_match:
            return asset_media_url(uuid.UUID(asset_match.group(1)))

        gen = _GENERATED_URL_RE.search(url)
        if gen:
            filename = safe_filename(gen.group(1))
            path = GENERATED_ROOT / filename
            if path.is_file():
                ingested, url_map, _ = await self.ingest_sd_output_files(
                    f"/media/generated/{filename}",
                    conversation_id=conversation_id,
                )
                if ingested:
                    return ingested[0]
            return url

        if url.startswith("http://") or url.startswith("https://") or url.startswith("/media/"):
            return await self.normalize_image_url(url, conversation_id=conversation_id)

        return url

    async def enrich_message_content_json(
        self,
        content_json: dict | None,
        *,
        conversation_id: uuid.UUID | None,
        content_text: str | None = None,
    ) -> tuple[dict | None, str | None]:
        """
        Нормализовать URL изображений для API / UI.

        Returns:
            (content_json, content_text) — content_text переписан при необходимости.
        """
        if not content_json and not content_text:
            return content_json, content_text

        from app.services.message_builder import (
            rewrite_media_urls_in_text,
            strip_markdown_images,
        )

        cj = dict(content_json or {})
        changed = False
        url_map: dict[str, str] = {}

        asset_ids: list[str] = list(cj.get("image_asset_ids") or [])
        images: list[str] = list(cj.get("images") or [])

        if asset_ids and not images:
            images = [
                asset_media_url(uid)
                for aid in asset_ids
                if (uid := _safe_uuid(aid)) is not None
            ]

        candidates: list[str] = list(dict.fromkeys(images))
        if content_text:
            for match in _MARKDOWN_IMG_RE.finditer(content_text):
                url = match.group(1).strip()
                if url and url not in candidates:
                    candidates.append(url)

        new_images: list[str] = []
        new_asset_ids: list[str] = []
        for raw in candidates:
            fixed = await self.ensure_asset_url(raw, conversation_id=conversation_id)
            aid = parse_asset_id_from_url(fixed)
            if aid:
                if await self._repo.get_by_id(aid) is None:
                    if fixed != raw:
                        changed = True
                    continue
                new_asset_ids.append(str(aid))
            new_images.append(fixed)
            if fixed != raw:
                url_map[raw] = fixed
                changed = True

        if new_images != images or candidates != images:
            changed = True
        cj["images"] = new_images
        if new_asset_ids:
            cj["image_asset_ids"] = list(dict.fromkeys(new_asset_ids))

        parts = cj.get("parts")
        if isinstance(parts, list):
            new_parts = []
            for part in parts:
                p = dict(part)
                if p.get("type") == "image_url" and p.get("image_url", {}).get("url"):
                    old = p["image_url"]["url"]
                    fixed = await self.ensure_asset_url(old, conversation_id=conversation_id)
                    p["image_url"] = dict(p["image_url"])
                    p["image_url"]["url"] = fixed
                    aid = parse_asset_id_from_url(fixed)
                    if aid:
                        p["asset_id"] = str(aid)
                    if fixed != old:
                        url_map[old] = fixed
                        changed = True
                new_parts.append(p)
            cj["parts"] = new_parts

        new_text = content_text
        if content_text and url_map:
            new_text = rewrite_media_urls_in_text(content_text, url_map)
            if new_text != content_text:
                changed = True

        if new_images and new_text:
            stripped = strip_markdown_images(new_text)
            if stripped != new_text:
                new_text = stripped
                changed = True

        if not changed:
            return content_json, content_text
        return cj, new_text


def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/png")


def _name_from_url(url: str) -> str:
    path = urlparse(url).path
    name = Path(path).name
    return name or "image"


def _make_thumb_bytes(data: bytes, max_size: tuple[int, int] = (512, 512)) -> bytes | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.thumbnail(max_size)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
    except Exception as exc:
        logger.warning("Миниатюра: %s", exc)
        return None


async def _fetch_url_bytes(url: str) -> tuple[bytes, str]:
    """Скачать изображение по HTTP(S) с лимитом размера."""
    async with httpx.AsyncClient(
        timeout=settings.request_timeout,
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "image/png").split(";")[0]
            if not content_type.startswith("image/"):
                raise ValueError(f"Не изображение: {content_type}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > _MAX_IMPORT_BYTES:
                    raise ValueError("Изображение слишком большое")
                chunks.append(chunk)
            return b"".join(chunks), content_type


def _safe_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _generated_url_variants(filename: str) -> list[str]:
    """Все варианты URL для одного generated-файла (для замены в тексте)."""
    safe = safe_filename(filename)
    path = f"/media/generated/{safe}"
    base = settings.public_base_url.rstrip("/")
    return list({
        path,
        f"{base}{path}",
        f"URL: {base}{path}",
        f"URL: {path}",
    })


def parse_asset_id_from_url(url: str) -> uuid.UUID | None:
    """Извлечь UUID media asset из URL, если есть."""
    m = _ASSET_URL_RE.search(url)
    if not m:
        return None
    try:
        return uuid.UUID(m.group(1))
    except ValueError:
        return None
