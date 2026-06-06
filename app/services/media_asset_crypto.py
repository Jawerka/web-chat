"""
Расшифровка MediaAsset и подготовка записей галереи загрузок.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import GalleryKind, MediaAsset, User
from app.db.repositories import MediaAssetRepository, UserRepository
from app.security.media_encryption import (
    decrypt_blob,
    encrypt_blob,
    encryption_version_encrypted,
    encryption_version_plain,
)
from app.integrations.sd_filename import resolve_upload_display_name
from app.services.sd_metadata import SdMetadata, extract_sd_metadata_from_bytes


def decrypt_asset_data(asset: MediaAsset, user: User | None) -> bytes:
    """Plaintext data; расшифровка при encryption_version=1."""
    if asset.encryption_version == 0:
        return asset.data
    if user is None or user.media_token is None:
        raise ValueError("media_token required for decrypt")
    return decrypt_blob(
        asset.data,
        media_token=user.media_token,
        auth_secret=settings.auth_secret,
        asset_id=asset.id,
    )


def decrypt_asset_thumb(asset: MediaAsset, user: User | None) -> bytes | None:
    if asset.thumb_data is None:
        return None
    if asset.encryption_version == 0:
        return asset.thumb_data
    if user is None or user.media_token is None:
        raise ValueError("media_token required for decrypt")
    return decrypt_blob(
        asset.thumb_data,
        media_token=user.media_token,
        auth_secret=settings.auth_secret,
        asset_id=asset.id,
    )


async def load_owner_for_asset(session: AsyncSession, asset: MediaAsset) -> User | None:
    if asset.owner_user_id is None:
        return None
    return await UserRepository(session).get_by_id(asset.owner_user_id)


async def encrypt_upload_payload(
    session: AsyncSession,
    user: User,
    *,
    data: bytes,
    thumb_data: bytes | None,
    mime_type: str,
    original_name: str | None,
    sd: SdMetadata | None,
    gallery_sort_order: int | None = None,
) -> MediaAsset:
    """Создать MediaAsset gallery_kind=upload с шифрованием."""
    if user.media_token is None:
        raise ValueError("user.media_token missing")
    asset_id = uuid.uuid4()
    enc_data = encrypt_blob(
        data,
        media_token=user.media_token,
        auth_secret=settings.auth_secret,
        asset_id=asset_id,
    )
    enc_thumb = None
    if thumb_data is not None:
        enc_thumb = encrypt_blob(
            thumb_data,
            media_token=user.media_token,
            auth_secret=settings.auth_secret,
            asset_id=asset_id,
        )
    now = datetime.now(UTC)
    repo = MediaAssetRepository(session)
    return await repo.create(
        data=enc_data,
        mime_type=mime_type,
        original_name=original_name,
        thumb_data=enc_thumb,
        asset_id=asset_id,
        owner_user_id=user.id,
        gallery_kind=GalleryKind.UPLOAD.value,
        encryption_version=encryption_version_encrypted(),
        sd_prompt=sd.prompt if sd else None,
        sd_negative=sd.negative if sd else None,
        sd_params=sd.params if sd else None,
        sd_meta_extracted_at=now if sd and sd.has_metadata else None,
        gallery_sort_order=gallery_sort_order,
    )


def apply_sd_metadata_to_asset(asset: MediaAsset, sd: SdMetadata | None) -> None:
    if sd is None or not sd.has_metadata:
        return
    asset.sd_prompt = sd.prompt
    asset.sd_negative = sd.negative
    asset.sd_params = sd.params
    asset.sd_meta_extracted_at = datetime.now(UTC)


async def copy_generation_to_upload(
    session: AsyncSession,
    user: User,
    source: MediaAsset,
) -> MediaAsset:
    """Копия generation → upload (encrypted)."""
    owner = await load_owner_for_asset(session, source)
    decrypt_user = owner or user
    plain = decrypt_asset_data(source, decrypt_user)
    thumb_plain = decrypt_asset_thumb(source, decrypt_user)
    sd = None
    if source.sd_prompt or source.sd_negative or source.sd_params:
        sd = SdMetadata(
            prompt=source.sd_prompt or "",
            negative=source.sd_negative or "",
            params=source.sd_params or "",
        )
    else:
        sd = extract_sd_metadata_from_bytes(plain)
    repo = MediaAssetRepository(session)
    sort_order = None
    if await repo.upload_gallery_has_custom_order(user.id):
        sort_order = (await repo.max_upload_sort_order(user.id)) + 1
    display_name = resolve_upload_display_name(
        plain,
        mime_type=source.mime_type,
        fallback_name=source.original_name,
        created_at=source.created_at,
    )
    return await encrypt_upload_payload(
        session,
        user,
        data=plain,
        thumb_data=thumb_plain,
        mime_type=source.mime_type,
        original_name=display_name,
        sd=sd,
        gallery_sort_order=sort_order,
    )


def gallery_kind_default_for_conversation(conversation_id: uuid.UUID | None) -> str:
    if conversation_id is not None:
        return GalleryKind.CHAT.value
    return GalleryKind.GENERATION.value


def encryption_version_plain_value() -> int:
    return encryption_version_plain()
