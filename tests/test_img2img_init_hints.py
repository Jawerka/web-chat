"""Тесты подсказок init для img2img и fallback вложений."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import Attachment
from app.integrations.tool_executor import ToolExecutor
from app.db.models import Message, MessageRole
from app.services.message_builder import (
    append_img2img_init_hints,
    build_img2img_init_hint_text,
    collect_img2img_init_lines,
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
