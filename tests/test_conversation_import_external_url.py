"""External URL import for POST /api/conversations/from-image (booru extension contract)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.db.models import Attachment, Conversation, Message
from tests.helpers import format_test_conversation_title
from tests.test_conversation_import import MINIMAL_PNG


@pytest.mark.asyncio
async def test_from_image_external_url(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_url_bytes(url: str) -> tuple[bytes, str]:
        assert url.startswith("https://static1.e621.net/")
        return MINIMAL_PNG, "image/png"

    monkeypatch.setattr(
        "app.services.media_service._fetch_url_bytes",
        fake_fetch_url_bytes,
    )

    title = format_test_conversation_title("from_image_external_url")
    tags = "anthro, dragon, solo"
    image_url = "https://static1.e621.net/data/54/24/542479e5218cca95313411c5ef0d9b13.jpg"

    r = await client.post(
        "/api/conversations/from-image",
        json={
            "title": title,
            "text": tags,
            "preset_slug": "img2img",
            "image": {"url": image_url},
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["composer_text"] == tags
    assert data["chat_url"] == f"/?conv={data['conversation_id']}"
    assert len(data["attachments"]) == 1

    from app.db import session as db_session
    import uuid

    conv_id = uuid.UUID(data["conversation_id"])
    async with db_session.async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None
        assert conv.composer_draft_text == tags

        att_rows = await session.execute(
            select(Attachment).where(Attachment.conversation_id == conv_id),
        )
        assert len(list(att_rows.scalars().all())) == 1

        msg_count = await session.scalar(
            select(func.count()).select_from(Message).where(Message.conversation_id == conv_id),
        )
        assert msg_count == 0
