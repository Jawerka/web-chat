"""Пакетное шифрование legacy MediaAsset."""

from __future__ import annotations

import base64

import pytest

from app.db.models import GalleryKind
from app.db.repositories import MediaAssetRepository
from app.db.session import async_session_factory
from app.services.gallery_owner import ensure_user_media_token, require_gallery_owner_user
from app.services.gallery_reencrypt_service import reencrypt_plaintext_batch
from app.services.media_service import MediaService

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_reencrypt_plaintext_generation_asset() -> None:
    async with async_session_factory() as session:
        user = await require_gallery_owner_user(session, None)
        await ensure_user_media_token(user)
        media = MediaService(session)
        asset = await media.create_from_bytes(
            MINIMAL_PNG,
            "image/png",
            original_name="plain.png",
        )
        asset.gallery_kind = GalleryKind.GENERATION.value
        asset.owner_user_id = user.id
        asset.encryption_version = 0
        await session.flush()
        aid = asset.id

        stats = await reencrypt_plaintext_batch(
            session,
            owner_user_id=user.id,
            limit=10,
        )
        await session.commit()
        assert stats["reencrypted"] >= 1

        row = await MediaAssetRepository(session).get_by_id(aid)
        assert row is not None
        assert row.encryption_version == 1
        assert row.data != MINIMAL_PNG
