"""Тесты media registry (P1.5)."""

from __future__ import annotations

import uuid

import pytest

from app.db import session as db_session
from app.services.media_registry import MediaRegistry


@pytest.mark.asyncio
async def test_register_image_and_list_metadata(client) -> None:
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    async with db_session.async_session_factory() as session:
        registry = MediaRegistry(session)
        reg = await registry.register_image(
            png,
            "image/png",
            original_name="tiny.png",
        )
        await session.commit()
        assert reg.url.startswith("/media/asset/")
        meta = await registry.list_gallery_metadata(limit=10)
        assert any(str(m.id) == str(reg.id) for m in meta)


@pytest.mark.asyncio
async def test_delete_asset_raises_when_missing(client) -> None:
    async with db_session.async_session_factory() as session:
        registry = MediaRegistry(session)
        with pytest.raises(FileNotFoundError):
            await registry.delete_asset(uuid.uuid4())


def test_disk_filename_claimed_by_db() -> None:
    names = {"photo.png", "other.jpg"}
    assert MediaRegistry.disk_filename_claimed_by_db("photo.png", names)
    assert not MediaRegistry.disk_filename_claimed_by_db("orphan.png", names)
