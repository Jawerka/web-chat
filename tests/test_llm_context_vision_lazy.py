"""P4.3: URL-first vision в сборке контекста; llm_data без загрузки data BLOB."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db import session as db_session
from app.db.models import MessageRole
from app.db.repositories import MediaAssetRepository, MessageRepository
from app.services.llm_context import build_conversation_llm_context
from app.services.media_service import MediaService
from tests.helpers import api_create_conversation, repo_create_conversation

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8 + b"\xff\xd9"


@pytest.mark.asyncio
async def test_get_llm_bytes_uses_cached_llm_data_without_get_by_id(
    client,
    test_conv_title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        asset = await media.create_from_bytes(_PNG, "image/png", conversation_id=conv_id)
        aid = asset.id
        await MediaAssetRepository(session).set_llm_data(aid, _JPEG)
        await session.commit()

    get_by_id = AsyncMock(side_effect=AssertionError("get_by_id must not load full BLOB"))
    monkeypatch.setattr(MediaAssetRepository, "get_by_id", get_by_id)

    async with db_session.async_session_factory() as session:
        data, mime = await MediaService(session).get_llm_bytes(aid)

    assert data == _JPEG
    assert "jpeg" in mime
    get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_conversation_llm_context_image_parts_are_llm_urls(
    client,
    test_conv_title: str,
    repo_conv_title: str,
) -> None:
    """Сборка контекста: image_url → /llm, без inline base64."""
    async with db_session.async_session_factory() as session:
        from app.db.repositories import ConversationRepository, PresetRepository

        preset_repo = PresetRepository(session)
        presets = await preset_repo.list_all()
        conv = await repo_create_conversation(session, presets[0].id, repo_conv_title)
        conv_id = conv.id

        media = MediaService(session)
        asset = await media.create_from_bytes(_PNG, "image/png", conversation_id=conv_id)
        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=conv_id,
            role=MessageRole.USER,
            content_text="с картинкой",
            content_json={
                "parts": [
                    {"type": "text", "text": "с картинкой"},
                    {
                        "type": "image_url",
                        "asset_id": str(asset.id),
                        "image_url": {"url": f"/media/asset/{asset.id}"},
                    },
                ],
            },
        )
        await session.commit()

    async with db_session.async_session_factory() as session:
        ctx = await build_conversation_llm_context(session, conv_id)

    user_msgs = [m for m in ctx["messages"] if m.get("role") == "user"]
    assert user_msgs
    parts = user_msgs[-1]["content"]
    assert isinstance(parts, list)
    img_parts = [p for p in parts if p.get("type") == "image_url"]
    assert img_parts
    url = img_parts[0]["image_url"]["url"]
    assert "/llm" in url
    assert not url.startswith("data:")


@pytest.mark.asyncio
async def test_build_context_calls_sanitize(
    client,
    test_conv_title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])
    sanitize = AsyncMock(side_effect=lambda _s, msgs: msgs)
    monkeypatch.setattr(
        "app.services.llm_context.sanitize_llm_messages_for_vision",
        sanitize,
    )

    async with db_session.async_session_factory() as session:
        await build_conversation_llm_context(session, conv_id)

    sanitize.assert_awaited_once()
