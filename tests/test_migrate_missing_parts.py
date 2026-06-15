"""Тесты миграции parts из attachments."""

from __future__ import annotations

import uuid

from app.db.models import Attachment
from app.scripts.migrate_missing_parts import _has_image_parts
from app.services.message_builder import build_user_content


def test_has_image_parts_false_when_empty() -> None:
    assert _has_image_parts(None) is False
    assert _has_image_parts({"parts": [{"type": "text", "text": "hi"}]}) is False


def test_has_image_parts_true() -> None:
    assert _has_image_parts(
        {"parts": [{"type": "image_url", "image_url": {"url": "/media/asset/x"}}]},
    )


def test_build_user_content_from_attachments_for_migration() -> None:
    """Логика миграции: parts из image-attachments."""
    att = Attachment(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        original_name="ref.png",
        mime_type="image/png",
        size_bytes=10,
        storage_path="",
        media_asset_id=uuid.uuid4(),
    )
    parts = build_user_content("перерисуй", [att])
    assert any(p.get("type") == "image_url" for p in parts)
    assert parts[0]["type"] == "text"
    assert any(
        p.get("type") == "text" and "[Изображение:" in (p.get("text") or "")
        for p in parts
    )
