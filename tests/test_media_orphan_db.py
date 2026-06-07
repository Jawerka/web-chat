"""P2.4: orphan MediaAsset в БД и dedup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.db import session as db_session
from app.db.models import GalleryKind, MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository
from app.services.gallery_service import cleanup_orphan_media_assets
from app.services.media_reference_index import collect_referenced_asset_ids
from app.services.media_registry import MediaRegistry

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_collect_referenced_from_message(client: AsyncClient) -> None:
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(
            title="ref-test",
            preset_id=preset.id,
        )
        registry = MediaRegistry(session)
        reg = await registry.register_image(_TINY_PNG, "image/png", original_name="ref.png")
        await MessageRepository(session).create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content_text="",
            content_json={
                "images": [reg.url],
                "image_asset_ids": [str(reg.id)],
            },
        )
        await session.commit()
        asset_id = reg.id

    async with db_session.async_session_factory() as session:
        refs = await collect_referenced_asset_ids(session)
    assert asset_id in refs


@pytest.mark.asyncio
async def test_orphan_media_asset_deleted_when_unreferenced(client: AsyncClient) -> None:
    async with db_session.async_session_factory() as session:
        registry = MediaRegistry(session)
        reg = await registry.register_image(
            _TINY_PNG,
            "image/png",
            original_name="lonely.png",
        )
        asset_id = reg.id
        asset = await registry.get_by_id(asset_id)
        assert asset is not None
        asset.created_at = datetime.now(UTC) - timedelta(hours=48)
        await session.flush()
        await session.commit()

    async with db_session.async_session_factory() as session:
        stats = await cleanup_orphan_media_assets(
            session,
            dry_run=False,
            min_age_hours=1,
        )
        await session.commit()
    assert stats["deleted"] >= 1

    async with db_session.async_session_factory() as session:
        assert await MediaRegistry(session).get_by_id(asset_id) is None


@pytest.mark.asyncio
async def test_dedup_removes_older_duplicate(client: AsyncClient) -> None:
    async with db_session.async_session_factory() as session:
        registry = MediaRegistry(session)
        old_reg = await registry.register_image(
            _TINY_PNG,
            "image/png",
            original_name="dup.png",
        )
        new_reg = await registry.register_image(
            _TINY_PNG,
            "image/png",
            original_name="dup.png",
        )
        old_asset = await registry.get_by_id(old_reg.id)
        new_asset = await registry.get_by_id(new_reg.id)
        assert old_asset is not None and new_asset is not None
        old_asset.created_at = datetime.now(UTC) - timedelta(hours=72)
        new_asset.created_at = datetime.now(UTC) - timedelta(hours=48)
        await session.flush()
        old_id = old_reg.id
        new_id = new_reg.id
        await session.commit()

    async with db_session.async_session_factory() as session:
        stats = await cleanup_orphan_media_assets(
            session,
            dry_run=False,
            min_age_hours=1,
            dedup=True,
        )
        await session.commit()
    assert stats["deduped"] >= 1

    async with db_session.async_session_factory() as session:
        reg = MediaRegistry(session)
        assert await reg.get_by_id(old_id) is None
        assert await reg.get_by_id(new_id) is not None


@pytest.mark.asyncio
async def test_upload_gallery_never_auto_deleted(client: AsyncClient) -> None:
    """gallery_kind=upload не удаляется orphan cleanup (долгосрочное хранилище)."""
    async with db_session.async_session_factory() as session:
        registry = MediaRegistry(session)
        reg = await registry.register_image(
            _TINY_PNG,
            "image/png",
            original_name="upload-keep.png",
            gallery_kind=GalleryKind.UPLOAD.value,
        )
        asset_id = reg.id
        asset = await registry.get_by_id(asset_id)
        assert asset is not None
        asset.created_at = datetime.now(UTC) - timedelta(hours=48)
        await session.flush()
        await session.commit()

    async with db_session.async_session_factory() as session:
        stats = await cleanup_orphan_media_assets(
            session,
            dry_run=False,
            min_age_hours=1,
        )
        await session.commit()

    assert stats["deleted"] == 0

    async with db_session.async_session_factory() as session:
        assert await MediaRegistry(session).get_by_id(asset_id) is not None
