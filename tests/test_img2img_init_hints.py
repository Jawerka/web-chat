"""Тесты подсказок init для img2img и fallback вложений."""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.config import settings
from app.db.models import Attachment
from app.integrations.tool_executor import ToolExecutor, ToolResult
from app.db.models import Message, MessageRole
from app.services.message_builder import (
    append_img2img_init_hints,
    build_img2img_init_hint_text,
    collect_img2img_init_lines,
)

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_build_img2img_init_hint_text() -> None:
    att_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    att = Attachment(
        id=att_id,
        conversation_id=uuid.uuid4(),
        message_id=None,
        original_name="ref.png",
        mime_type="image/png",
        size_bytes=100,
        storage_path="",
        media_asset_id=asset_id,
    )
    hint = build_img2img_init_hint_text([att])
    assert str(att_id) in hint
    assert str(asset_id) in hint
    assert "init_image_url=" in hint


def test_append_img2img_init_hints_adds_text_part() -> None:
    att = Attachment(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        message_id=None,
        original_name="a.png",
        mime_type="image/png",
        size_bytes=1,
        storage_path="",
        media_asset_id=uuid.uuid4(),
    )
    parts = append_img2img_init_hints([{"type": "text", "text": "перерисуй"}], [att])
    assert len(parts) == 2
    assert "attachment_id=" in parts[-1]["text"]


def test_build_img2img_init_hint_from_parts_only() -> None:
    asset_id = uuid.uuid4()
    parts = [
        {"type": "text", "text": "перерисуй"},
        {
            "type": "image_url",
            "image_url": {"url": f"/media/asset/{asset_id}"},
            "asset_id": str(asset_id),
        },
    ]
    lines = collect_img2img_init_lines([], parts)
    assert any("init_image_url=" in line for line in lines)
    assert str(asset_id) in "\n".join(lines)


@pytest.mark.asyncio
async def test_resolve_user_message_init_from_parts() -> None:
    msg_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    msg = Message(
        id=msg_id,
        conversation_id=uuid.uuid4(),
        role=MessageRole.USER,
        content_text="перерисуй",
        content_json={
            "parts": [
                {"type": "text", "text": "перерисуй"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"http://192.168.1.1:8090/media/asset/{asset_id}"},
                },
            ],
        },
    )

    session = AsyncMock()
    att_repo = MagicMock()
    att_repo.list_for_message = AsyncMock(return_value=[])
    msg_repo = MagicMock()
    msg_repo.get_by_id = AsyncMock(return_value=msg)

    executor = ToolExecutor(session, source_user_message_id=msg_id)
    executor._load_init_image = AsyncMock(return_value=(b"pngbytes", "from-part.png"))

    from app.integrations import tool_executor as te_mod

    orig_att = te_mod.AttachmentRepository
    orig_msg = te_mod.MessageRepository
    te_mod.AttachmentRepository = lambda _s: att_repo
    te_mod.MessageRepository = lambda _s: msg_repo
    try:
        loaded = await executor._resolve_user_message_init()
    finally:
        te_mod.AttachmentRepository = orig_att
        te_mod.MessageRepository = orig_msg

    assert loaded == (b"pngbytes", "from-part.png")


@pytest.mark.asyncio
async def test_init_from_user_message_attachments() -> None:
    msg_id = uuid.uuid4()
    att_id = uuid.uuid4()
    att = Attachment(
        id=att_id,
        conversation_id=uuid.uuid4(),
        message_id=msg_id,
        original_name="user.png",
        mime_type="image/png",
        size_bytes=10,
        storage_path="",
        media_asset_id=uuid.uuid4(),
    )

    session = AsyncMock()
    repo = MagicMock()
    repo.list_for_message = AsyncMock(return_value=[att])

    executor = ToolExecutor(session, source_user_message_id=msg_id)
    executor._load_init_image = AsyncMock(return_value=(b"pngbytes", "user.png"))

    from app.integrations import tool_executor as te_mod

    original_repo = te_mod.AttachmentRepository
    te_mod.AttachmentRepository = lambda _s: repo
    try:
        loaded = await executor._resolve_user_message_init()
    finally:
        te_mod.AttachmentRepository = original_repo

    assert loaded == (b"pngbytes", "user.png")
    executor._load_init_image.assert_awaited_once_with(attachment_id=att_id)


@pytest.mark.asyncio
async def test_pinned_user_init_cached_across_calls() -> None:
    """Несколько img2img в одном ходе читают init из кэша."""
    msg_id = uuid.uuid4()
    session = AsyncMock()

    executor = ToolExecutor(session, source_user_message_id=msg_id)
    executor._resolve_user_message_init = AsyncMock(return_value=(b"pngbytes", "user.png"))
    executor._run_sd_image_tool = AsyncMock(
        return_value=ToolResult(content="ok", image_urls=[]),
    )

    for _ in range(3):
        await executor._img2img({"prompt": "test", "attachment_id": str(uuid.uuid4())})

    assert executor._resolve_user_message_init.await_count == 1


@pytest.mark.asyncio
async def test_img2img_prefers_server_init_over_llm_url() -> None:
    """При source_user_message_id серверный init имеет приоритет над URL от LLM."""
    msg_id = uuid.uuid4()
    session = AsyncMock()

    executor = ToolExecutor(session, source_user_message_id=msg_id)
    executor._resolve_user_message_init = AsyncMock(return_value=(b"from-server", "srv.png"))
    executor._load_init_image = AsyncMock(return_value=(b"from-llm", "llm.png"))
    executor._run_sd_image_tool = AsyncMock(
        return_value=ToolResult(content="ok", image_urls=[]),
    )

    await executor._img2img(
        {
            "prompt": "test",
            "init_image_url": "http://bad.example/media/asset/00000000-0000-0000-0000-000000000099",
        },
    )

    executor._load_init_image.assert_not_awaited()
    call_args = executor._run_sd_image_tool.call_args
    assert call_args[0][1]["init_image_bytes"] == b"from-server"
    assert call_args[0][1]["init_source_name"] == "srv.png"


@pytest.mark.asyncio
async def test_load_init_image_upload_gallery_attachment(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """img2img init из gallery_kind=upload (encrypted) через attachment_id."""
    from app.db import session as db_session
    from app.db.repositories import AttachmentRepository, ConversationRepository, PresetRepository
    from app.services.auth_service import ensure_bootstrap_admin
    from app.services.gallery_owner import ensure_user_media_token
    from app.services.media_asset_crypto import encrypt_upload_payload

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_secret", "test-auth-secret-key-32chars-minimum!!")

    async with db_session.async_session_factory() as session:
        user = await ensure_bootstrap_admin(session)
        await ensure_user_media_token(user)
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(
            title="[pytest] img2img upload init",
            preset_id=preset.id,
            owner_user_id=user.id,
        )
        asset = await encrypt_upload_payload(
            session,
            user,
            data=MINIMAL_PNG,
            thumb_data=None,
            mime_type="image/png",
            original_name="upload-ref.png",
            sd=None,
        )
        att = await AttachmentRepository(session).create(
            attachment_id=uuid.uuid4(),
            original_name="upload-ref.png",
            mime_type="image/png",
            size_bytes=len(MINIMAL_PNG),
            storage_path="",
            conversation_id=conv.id,
            media_asset_id=asset.id,
        )
        await session.commit()
        conv_id = conv.id
        att_id = att.id

    async with db_session.async_session_factory() as session:
        executor = ToolExecutor(session, conversation_id=conv_id)
        data, name = await executor._load_init_image(attachment_id=att_id)

    assert data == MINIMAL_PNG
    assert name == "upload-ref.png"
