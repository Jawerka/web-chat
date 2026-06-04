"""
Фоновое (пакетное) шифрование legacy MediaAsset с encryption_version=0.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GalleryKind, MediaAsset
from app.db.repositories import UserRepository
from app.security.media_encryption import encrypt_blob
from app.services.gallery_owner import ensure_user_media_token
from app.services.media_asset_crypto import encryption_version_encrypted

logger = logging.getLogger(__name__)


async def reencrypt_plaintext_batch(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID | None = None,
    gallery_kinds: tuple[str, ...] | None = None,
    limit: int = 50,
) -> dict[str, int]:
    """
    Перешифровать до ``limit`` активов с encryption_version=0 in-place.

    Требует ``users.media_token`` у владельца актива.
    """
    from app.config import settings

    kinds = gallery_kinds or (
        GalleryKind.GENERATION.value,
        GalleryKind.CHAT.value,
    )
    cap = max(1, min(500, int(limit)))
    stmt = (
        select(MediaAsset)
        .where(
            MediaAsset.encryption_version == 0,
            MediaAsset.gallery_kind.in_(kinds),
        )
        .order_by(MediaAsset.created_at.asc())
        .limit(cap)
    )
    if owner_user_id is not None:
        stmt = stmt.where(MediaAsset.owner_user_id == owner_user_id)

    result = await session.execute(stmt)
    assets = list(result.scalars().all())
    user_repo = UserRepository(session)
    done = 0
    skipped = 0

    for asset in assets:
        uid = asset.owner_user_id or owner_user_id
        if uid is None:
            skipped += 1
            continue
        user = await user_repo.get_by_id(uid)
        if user is None:
            skipped += 1
            continue
        await ensure_user_media_token(user)
        if not user.media_token:
            skipped += 1
            continue
        try:
            enc_data = encrypt_blob(
                asset.data,
                media_token=user.media_token,
                auth_secret=settings.auth_secret,
                asset_id=asset.id,
            )
            enc_thumb = None
            if asset.thumb_data:
                enc_thumb = encrypt_blob(
                    asset.thumb_data,
                    media_token=user.media_token,
                    auth_secret=settings.auth_secret,
                    asset_id=asset.id,
                )
            asset.data = enc_data
            asset.thumb_data = enc_thumb
            asset.encryption_version = encryption_version_encrypted()
            done += 1
        except Exception:
            logger.exception("reencrypt failed asset=%s", asset.id)
            skipped += 1

    if done:
        await session.flush()
    return {"reencrypted": done, "skipped": skipped, "candidates": len(assets)}
