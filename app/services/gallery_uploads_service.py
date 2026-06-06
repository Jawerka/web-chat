"""
Галерея загрузок: список, upload, delete, promote из генераций.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GalleryKind, MediaAsset
from app.db.repositories import MediaAssetRepository, MediaFavoriteRepository
from app.integrations.media_utils import (
    asset_media_url,
    asset_thumb_url,
    is_image_mime,
    make_asset_thumb_bytes,
    resolve_generated_file,
    safe_filename,
    sniff_image_mime,
)
from app.integrations.sd_filename import resolve_upload_display_name
from app.services.gallery_owner import require_gallery_owner_user
from app.services.media_asset_crypto import (
    apply_sd_metadata_to_asset,
    copy_generation_to_upload,
    encrypt_upload_payload,
)
from app.services.request_user import RequestUser
from app.services.sd_metadata import extract_sd_metadata_from_bytes

GALLERY_UPLOADS_MAX = 5000


@dataclass(slots=True)
class UploadGalleryItem:
    """Элемент таблицы галереи загрузок."""

    id: str
    filename: str
    url: str
    thumb_url: str
    size_kb: float
    mtime: float
    is_favorite: bool = False
    favorite_at: float | None = None
    sd_prompt: str = ""
    sd_negative: str = ""
    sd_params: str = ""
    has_metadata: bool = False
    gallery_sort_order: int | None = None

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "url": self.url,
            "thumb_url": self.thumb_url,
            "size_kb": self.size_kb,
            "mtime": self.mtime,
            "source": "db",
            "is_favorite": self.is_favorite,
            "favorite_at": self.favorite_at,
            "sd_prompt": self.sd_prompt,
            "sd_negative": self.sd_negative,
            "sd_params": self.sd_params,
            "has_metadata": self.has_metadata,
            "gallery_sort_order": self.gallery_sort_order,
        }


def _sort_upload_items(items: list[UploadGalleryItem]) -> None:
    has_custom = any(i.gallery_sort_order is not None for i in items)
    if has_custom:
        items.sort(
            key=lambda x: (
                x.gallery_sort_order
                if x.gallery_sort_order is not None
                else 10**9,
                -x.mtime,
            ),
        )
        return
    items.sort(
        key=lambda x: (1 if x.is_favorite else 0, x.favorite_at or 0.0, x.mtime),
        reverse=True,
    )


def _item_from_meta(meta, *, favorite_map: dict[str, datetime]) -> UploadGalleryItem:
    name = meta.original_name or f"{meta.id}.png"
    url = asset_media_url(meta.id)
    thumb = asset_thumb_url(meta.id) if meta.has_thumb else url
    key = f"db:{meta.id}"
    fav_dt = favorite_map.get(key)
    return UploadGalleryItem(
        id=str(meta.id),
        filename=name,
        url=url,
        thumb_url=thumb,
        size_kb=round(meta.size_bytes / 1024, 1),
        mtime=meta.created_at.timestamp(),
        is_favorite=fav_dt is not None,
        favorite_at=fav_dt.timestamp() if fav_dt else None,
        sd_prompt=meta.sd_prompt or "",
        sd_negative=meta.sd_negative or "",
        sd_params=meta.sd_params or "",
        has_metadata=meta.has_metadata,
        gallery_sort_order=meta.gallery_sort_order,
    )


async def list_upload_gallery(
    session: AsyncSession,
    *,
    request_user: RequestUser | None,
    limit: int = GALLERY_UPLOADS_MAX,
) -> list[UploadGalleryItem]:
    user = await require_gallery_owner_user(session, request_user)
    cap = max(1, min(GALLERY_UPLOADS_MAX, int(limit)))
    repo = MediaAssetRepository(session)
    rows = await repo.list_gallery_metadata(
        cap,
        owner_user_id=user.id,
        gallery_kind=GalleryKind.UPLOAD.value,
    )
    fav_map = await MediaFavoriteRepository(session).favorite_map(user.id)
    items = [_item_from_meta(r, favorite_map=fav_map) for r in rows if is_image_mime(r.mime_type)]
    _sort_upload_items(items)
    return items


async def reorder_upload_gallery(
    session: AsyncSession,
    *,
    request_user: RequestUser | None,
    ordered_ids: list[uuid.UUID],
) -> None:
    user = await require_gallery_owner_user(session, request_user)
    if not ordered_ids:
        return
    repo = MediaAssetRepository(session)
    try:
        await repo.set_upload_gallery_order(user.id, ordered_ids)
    except PermissionError as exc:
        raise PermissionError("forbidden") from exc


async def next_upload_sort_order(
    session: AsyncSession,
    owner_user_id: uuid.UUID,
) -> int | None:
    repo = MediaAssetRepository(session)
    if not await repo.upload_gallery_has_custom_order(owner_user_id):
        return None
    return (await repo.max_upload_sort_order(owner_user_id)) + 1


async def get_upload_item(
    session: AsyncSession,
    asset_id: uuid.UUID,
    *,
    request_user: RequestUser | None,
    extract: bool = False,
) -> UploadGalleryItem | None:
    user = await require_gallery_owner_user(session, request_user)
    asset = await MediaAssetRepository(session).get_by_id(asset_id)
    if asset is None or asset.gallery_kind != GalleryKind.UPLOAD.value:
        return None
    if asset.owner_user_id != user.id:
        return None
    if extract and not (asset.sd_prompt or asset.sd_negative or asset.sd_params):
        from app.services.media_asset_crypto import decrypt_asset_data

        plain = decrypt_asset_data(asset, user)
        sd = extract_sd_metadata_from_bytes(plain)
        if sd and sd.has_metadata:
            apply_sd_metadata_to_asset(asset, sd)
            new_name = resolve_upload_display_name(
                plain,
                mime_type=asset.mime_type,
                fallback_name=asset.original_name,
                created_at=asset.created_at,
            )
            if new_name and new_name != asset.original_name:
                asset.original_name = new_name
            await session.flush()
    from app.db.repositories import GalleryAssetMeta

    meta = GalleryAssetMeta(
        id=asset.id,
        mime_type=asset.mime_type,
        original_name=asset.original_name,
        created_at=asset.created_at,
        size_bytes=len(asset.data),
        has_thumb=asset.thumb_data is not None,
        gallery_kind=asset.gallery_kind,
        gallery_sort_order=asset.gallery_sort_order,
        sd_prompt=asset.sd_prompt,
        sd_negative=asset.sd_negative,
        sd_params=asset.sd_params,
    )
    fav_map = await MediaFavoriteRepository(session).favorite_map(user.id)
    return _item_from_meta(meta, favorite_map=fav_map)


async def upload_to_gallery(
    session: AsyncSession,
    *,
    request_user: RequestUser | None,
    data: bytes,
    mime_type: str,
    original_name: str | None,
) -> MediaAsset:
    if not is_image_mime(mime_type):
        raise ValueError("Только изображения")
    user = await require_gallery_owner_user(session, request_user)
    thumb = make_asset_thumb_bytes(data)
    sd = extract_sd_metadata_from_bytes(data)
    sort_order = await next_upload_sort_order(session, user.id)
    display_name = resolve_upload_display_name(
        data,
        mime_type=mime_type,
        fallback_name=original_name,
    )
    return await encrypt_upload_payload(
        session,
        user,
        data=data,
        thumb_data=thumb,
        mime_type=mime_type,
        original_name=display_name,
        sd=sd,
        gallery_sort_order=sort_order,
    )


async def delete_upload_asset(
    session: AsyncSession,
    asset_id: uuid.UUID,
    *,
    request_user: RequestUser | None,
) -> None:
    user = await require_gallery_owner_user(session, request_user)
    repo = MediaAssetRepository(session)
    asset = await repo.get_by_id(asset_id)
    if asset is None or asset.gallery_kind != GalleryKind.UPLOAD.value:
        raise FileNotFoundError(str(asset_id))
    if asset.owner_user_id != user.id:
        raise PermissionError("forbidden")
    await repo.delete(asset)


async def promote_generation_to_uploads(
    session: AsyncSession,
    asset_id: uuid.UUID,
    *,
    request_user: RequestUser | None,
) -> MediaAsset:
    user = await require_gallery_owner_user(session, request_user)
    repo = MediaAssetRepository(session)
    source = await repo.get_by_id(asset_id)
    if source is None:
        raise FileNotFoundError(str(asset_id))
    if source.gallery_kind == GalleryKind.UPLOAD.value:
        raise ValueError("Уже в галерее загрузок")
    if source.gallery_kind not in (
        GalleryKind.GENERATION.value,
        GalleryKind.CHAT.value,
        None,
    ):
        raise ValueError("Нельзя добавить в галерею загрузок")
    if source.owner_user_id is not None and source.owner_user_id != user.id:
        raise PermissionError("forbidden")
    return await copy_generation_to_upload(session, user, source)


async def promote_disk_to_uploads(
    session: AsyncSession,
    filename: str,
    *,
    request_user: RequestUser | None,
) -> MediaAsset:
    """Файл из data/generated/ → копия в галерею загрузок."""
    user = await require_gallery_owner_user(session, request_user)
    from app.integrations.media_utils import safe_generated_filename

    safe = safe_generated_filename(filename) or safe_filename(filename)
    path = resolve_generated_file(safe, thumbs=False)
    data = path.read_bytes()
    mime = sniff_image_mime(data) or "image/png"
    if not is_image_mime(mime):
        raise ValueError("Только изображения")
    sd = extract_sd_metadata_from_bytes(data)
    thumb = make_asset_thumb_bytes(data)
    sort_order = await next_upload_sort_order(session, user.id)
    display_name = resolve_upload_display_name(
        data,
        mime_type=mime,
        fallback_name=safe,
    )
    return await encrypt_upload_payload(
        session,
        user,
        data=data,
        thumb_data=thumb,
        mime_type=mime,
        original_name=display_name,
        sd=sd,
        gallery_sort_order=sort_order,
    )
