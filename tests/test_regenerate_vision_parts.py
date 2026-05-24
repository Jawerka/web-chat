"""Перегенерация: актуальные vision URL из вложений."""

from __future__ import annotations

import uuid

from app.db.models import Attachment
from app.integrations.media_utils import rewrite_image_url_for_llm
from app.services.message_builder import (
    build_img2img_init_hint_text,
    refresh_user_parts_for_regenerate,
)


def test_rewrite_absolute_asset_url_to_llm_variant() -> None:
    aid = uuid.uuid4()
    base = "http://192.168.88.44:8090"
    url = f"{base}/media/asset/{aid}/preview"
    out = rewrite_image_url_for_llm(url)
    assert out.endswith("/llm")
    assert str(aid) in out


def test_refresh_user_parts_rebuilds_from_attachments() -> None:
    att_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    att = Attachment(
        id=att_id,
        conversation_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        original_name="ref.png",
        mime_type="image/png",
        size_bytes=100,
        storage_path="",
        media_asset_id=asset_id,
    )
    stale_parts = [
        {"type": "text", "text": "перерисуй"},
        {
            "type": "image_url",
            "image_url": {"url": "http://wrong-host:9999/media/asset/deadbeef"},
        },
        {
            "type": "text",
            "text": "[Для img2img используйте эти параметры]\ninit_image_url=old",
        },
    ]
    fresh = refresh_user_parts_for_regenerate(
        stale_parts,
        [att],
        fallback_text="перерисуй",
    )
    assert fresh[0]["text"] == "перерисуй"
    img_parts = [p for p in fresh if p.get("type") == "image_url"]
    assert len(img_parts) == 1
    assert str(asset_id) in img_parts[0]["image_url"]["url"]
    assert "deadbeef" not in img_parts[0]["image_url"]["url"]
    assert not any(
        str(p.get("text", "")).startswith("[Для img2img") for p in fresh if p.get("type") == "text"
    )


def test_img2img_hint_uses_llm_url() -> None:
    asset_id = uuid.uuid4()
    att = Attachment(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        message_id=None,
        original_name="a.png",
        mime_type="image/png",
        size_bytes=1,
        storage_path="",
        media_asset_id=asset_id,
    )
    hint = build_img2img_init_hint_text([att])
    assert "/llm" in hint or str(asset_id) in hint
