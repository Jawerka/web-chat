"""Vision filter: проверка ассета без загрузки BLOB (P4.6)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.db import session as db_session
from app.db.repositories import MediaAssetRepository
from app.services.media_service import MediaService
from app.services.message_builder import filter_unreachable_image_parts
from tests.helpers import api_create_conversation

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.mark.asyncio
async def test_media_asset_repository_exists_without_full_row() -> None:
    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        asset = await media.create_from_bytes(_PNG, "image/png")
        await session.commit()
        aid = asset.id

        repo = MediaAssetRepository(session)
        assert await repo.exists(aid) is True
        assert await repo.exists(uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_filter_unreachable_skips_get_bytes(
    client: AsyncClient,
    test_conv_title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv = await api_create_conversation(client, test_conv_title)
    cid = uuid.UUID(conv["id"])

    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        asset = await media.create_from_bytes(
            _PNG,
            "image/png",
            conversation_id=cid,
        )
        await session.commit()
        aid = asset.id

    get_bytes_mock = AsyncMock()
    monkeypatch.setattr(MediaService, "get_bytes", get_bytes_mock)

    parts = [
        {"type": "text", "text": "hi"},
        {
            "type": "image_url",
            "asset_id": str(aid),
            "image_url": {"url": f"http://test/media/asset/{aid}/llm"},
        },
    ]
    missing_id = uuid.uuid4()
    parts_missing = [
        {
            "type": "image_url",
            "asset_id": str(missing_id),
            "image_url": {"url": f"http://test/media/asset/{missing_id}/llm"},
        },
    ]

    async with db_session.async_session_factory() as session:
        filtered = await filter_unreachable_image_parts(session, parts)
        dropped = await filter_unreachable_image_parts(session, parts_missing)

    assert len(filtered) == 2
    assert filtered[1].get("asset_id") == str(aid)
    assert dropped == []
    get_bytes_mock.assert_not_called()


@pytest.mark.asyncio
async def test_is_image_url_available_uses_exists_not_get_bytes(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = uuid.uuid4()
    exists_mock = AsyncMock(return_value=True)
    get_bytes_mock = AsyncMock()
    monkeypatch.setattr(MediaService, "asset_exists", exists_mock)
    monkeypatch.setattr(MediaService, "get_bytes", get_bytes_mock)

    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        ok = await media.is_image_url_available(f"/media/asset/{aid}/llm")

    assert ok is True
    exists_mock.assert_awaited_once_with(aid)
    get_bytes_mock.assert_not_called()
