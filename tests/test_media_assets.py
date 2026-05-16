"""Тесты хранения изображений в БД."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.db import session as db_session
from app.db.repositories import MediaAssetRepository
from app.services.media_service import MediaService

# 1×1 PNG
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.mark.asyncio
async def test_media_asset_serve(client: AsyncClient) -> None:
    conv = await client.post("/api/conversations", json={})
    cid = uuid.UUID(conv.json()["id"])

    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        asset = await media.create_from_bytes(
            _PNG,
            "image/png",
            conversation_id=cid,
        )
        await session.commit()
        aid = asset.id

    r = await client.get(f"/media/asset/{aid}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == _PNG

    thumb = await client.get(f"/media/asset/{aid}/thumb")
    assert thumb.status_code == 200


@pytest.mark.asyncio
async def test_upload_image_stored_in_db(client: AsyncClient) -> None:
    conv = await client.post("/api/conversations", json={})
    cid = conv.json()["id"]

    files = {"files": ("test.png", _PNG, "image/png")}
    up = await client.post(
        f"/api/upload?conversation_id={cid}",
        files=files,
    )
    assert up.status_code == 200
    att = up.json()["attachments"][0]
    assert "/media/asset/" in att["preview_url"]

    asset_id = uuid.UUID(att["preview_url"].rstrip("/").split("/")[-1])
    async with db_session.async_session_factory() as session:
        repo = MediaAssetRepository(session)
        asset = await repo.get_by_id(asset_id)
        assert asset is not None
        assert asset.data == _PNG


@pytest.mark.asyncio
async def test_list_messages_imports_legacy_generated_to_db(
    client: AsyncClient,
) -> None:
    """GET /messages импортирует legacy /media/generated в БД и сохраняет asset URL."""

    from app.db.models import MessageRole
    from app.db.repositories import ConversationRepository, MessageRepository
    from app.integrations.media_utils import GENERATED_ROOT, generated_media_url

    conv = await client.post("/api/conversations", json={})
    cid = uuid.UUID(conv.json()["id"])

    filename = f"test_legacy_{uuid.uuid4().hex[:8]}.png"
    path = GENERATED_ROOT / filename
    path.write_bytes(_PNG)
    legacy_url = generated_media_url(filename)

    async with db_session.async_session_factory() as session:
        conv_repo = ConversationRepository(session)
        conversation = await conv_repo.get_by_id(cid)
        assert conversation is not None
        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=cid,
            role=MessageRole.ASSISTANT,
            content_text=f"Картинка:\n\n![test]({legacy_url})",
            content_json={"images": [legacy_url]},
        )
        await session.commit()

    listed = await client.get(f"/api/conversations/{cid}/messages")
    assert listed.status_code == 200
    msgs = listed.json()
    assert len(msgs) == 1
    m = msgs[0]
    assert m["content_json"]["images"]
    assert m["content_json"]["images"][0].startswith("/media/asset/")
    assert m["content_json"]["image_asset_ids"]
    assert "![test]" not in (m["content_text"] or "")
    assert m["content_text"].strip() == "Картинка:"

    asset_id = uuid.UUID(m["content_json"]["image_asset_ids"][0])
    r = await client.get(f"/media/asset/{asset_id}")
    assert r.status_code == 200
    assert r.content == _PNG

    async with db_session.async_session_factory() as session:
        repo = MediaAssetRepository(session)
        asset = await repo.get_by_id(asset_id)
        assert asset is not None

    path.unlink(missing_ok=True)
