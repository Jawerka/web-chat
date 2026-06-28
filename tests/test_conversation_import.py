"""Тесты POST /api/conversations/from-image."""

from __future__ import annotations

import base64
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.db.models import Attachment, Conversation, Message
from app.integrations import media_utils
from tests.helpers import format_test_conversation_title

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_from_image_json_asset_id(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    from app.db import session as db_session
    from app.services.media_service import MediaService

    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        asset = await media.create_from_bytes(MINIMAL_PNG, "image/png", original_name="gal.png")
        await session.commit()
        asset_id = asset.id

    title = format_test_conversation_title("from_image_asset")
    r = await client.post(
        "/api/conversations/from-image",
        json={
            "title": title,
            "text": "опиши картинку",
            "preset_slug": "img2img",
            "image": {"asset_id": str(asset_id)},
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["title"] == title
    assert data["composer_text"] == "опиши картинку"
    assert data["chat_url"] == f"/?conv={data['conversation_id']}"
    assert len(data["attachments"]) == 1

    conv_id = uuid.UUID(data["conversation_id"])
    async with db_session.async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None
        assert conv.composer_draft_text == "опиши картинку"

        att_rows = await session.execute(
            select(Attachment).where(Attachment.conversation_id == conv_id),
        )
        attachments = list(att_rows.scalars().all())
        assert len(attachments) == 1
        assert attachments[0].media_asset_id == asset_id
        assert attachments[0].message_id is None

        msg_count = await session.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id == conv_id),
        )
        assert msg_count == 0


@pytest.mark.asyncio
async def test_from_image_multipart(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    title = format_test_conversation_title("from_image_multipart")
    r = await client.post(
        "/api/conversations/from-image",
        data={
            "text": "hello from curl",
            "title": title,
            "preset_slug": "default",
        },
        files=[("image", ("shot.png", MINIMAL_PNG, "image/png"))],
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["composer_text"] == "hello from curl"
    assert len(data["attachments"]) == 1

    conv_id = uuid.UUID(data["conversation_id"])
    from app.db import session as db_session

    async with db_session.async_session_factory() as session:
        msg_count = await session.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id == conv_id),
        )
        assert msg_count == 0


@pytest.mark.asyncio
async def test_from_image_disk_filename(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gen = tmp_path / "generated"
    thumbs = gen / "thumbs"
    gen.mkdir()
    thumbs.mkdir()
    (gen / "disk_one.png").write_bytes(MINIMAL_PNG)
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumbs)

    title = format_test_conversation_title("from_image_disk")
    r = await client.post(
        "/api/conversations/from-image",
        json={
            "title": title,
            "text": "disk import",
            "image": {"disk_filename": "disk_one.png"},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["composer_text"] == "disk import"


@pytest.mark.asyncio
async def test_from_image_missing_image(client: AsyncClient) -> None:
    r = await client.post(
        "/api/conversations/from-image",
        json={"text": "x", "image": {}},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_from_image_unknown_asset(client: AsyncClient) -> None:
    r = await client.post(
        "/api/conversations/from-image",
        json={
            "text": "x",
            "image": {"asset_id": "00000000-0000-0000-0000-000000000099"},
        },
    )
    assert r.status_code == 404
