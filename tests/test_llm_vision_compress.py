"""Сжатие изображений для LLM vision API."""

from __future__ import annotations

import base64
import io
import uuid

import pytest
from PIL import Image

from app.config import settings
from app.integrations.media_utils import (
    asset_llm_media_url,
    compress_image_for_llm,
    prepare_image_for_llm_vision,
    rewrite_image_url_for_llm,
)


def _rgb_png_bytes(width: int, height: int) -> bytes:
    import os

    img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=0)
    return buf.getvalue()


def test_compress_reduces_oversized_image() -> None:
    raw = _rgb_png_bytes(1800, 1800)
    limit = 500_000
    assert len(raw) > limit
    out, mime = compress_image_for_llm(raw, "image/png", max_bytes=limit)
    assert mime == "image/jpeg"
    assert len(out) <= limit
    assert len(out) < len(raw)


def test_compress_keeps_small_image() -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    out, mime = compress_image_for_llm(png, "image/png")
    assert out == png
    assert mime == "image/png"


def test_prepare_converts_webp_to_jpeg() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(10, 20, 30)).save(buf, format="WEBP", quality=80)
    webp = buf.getvalue()
    out, mime = prepare_image_for_llm_vision(webp, "image/webp")
    assert mime == "image/jpeg"
    assert out[:2] == b"\xff\xd8"
    assert _pil_image_ok(out)


def _pil_image_ok(data: bytes) -> bool:
    from app.integrations.media_utils import _pil_image_ok as ok

    return ok(data)


def test_rewrite_asset_url_to_llm_variant() -> None:
    aid = "14e11a27-e276-4779-8851-11fc024a39f5"
    base = settings.public_base_url.rstrip("/")
    rewritten = rewrite_image_url_for_llm(f"/media/asset/{aid}")
    assert rewritten == f"{base}/media/asset/{aid}/llm"
    assert rewrite_image_url_for_llm(f"{base}/media/asset/{aid}").endswith("/llm")
    assert asset_llm_media_url(uuid.UUID(aid)) == f"/media/asset/{aid}/llm"


@pytest.mark.asyncio
async def test_serve_asset_llm_endpoint(client) -> None:
    """GET /media/asset/{id}/llm отдаёт тело ≤ лимита для большого asset."""
    from app.db import session as db_session
    from app.services.media_service import MediaService
    from app.config import settings as app_settings

    raw = _rgb_png_bytes(2200, 2200)
    async with db_session.async_session_factory() as session:
        service = MediaService(session)
        asset = await service.create_from_bytes(raw, "image/png")
        await session.commit()
        asset_id = asset.id

    # Vision URL скачивается LLM без cookie — с доверенного IP (loopback в тестах).
    app_settings.auth_enabled = True
    app_settings.trusted_internal_allow_loopback = True
    from app.security.trusted_internal import refresh_trusted_internal_from_settings

    refresh_trusted_internal_from_settings()

    resp = await client.get(f"/media/asset/{asset_id}/llm")
    assert resp.status_code == 200
    assert len(resp.content) <= settings.llm_vision_max_bytes

    async with db_session.async_session_factory() as session:
        from app.db.repositories import MediaAssetRepository

        reloaded = await MediaAssetRepository(session).get_by_id(asset_id)
        assert reloaded is not None
        assert reloaded.llm_data is not None
        assert len(reloaded.llm_data) <= settings.llm_vision_max_bytes

    resp2 = await client.get(f"/media/asset/{asset_id}/llm")
    assert resp2.status_code == 200
    assert resp2.content == resp.content
    assert resp.headers["content-type"].startswith("image/")


@pytest.mark.asyncio
async def test_serve_asset_llm_webp_becomes_jpeg(client) -> None:
    """WebP во вложении → /llm отдаёт JPEG для llama-server."""
    from app.db import session as db_session
    from app.services.media_service import MediaService
    from app.config import settings as app_settings

    buf = io.BytesIO()
    Image.new("RGB", (128, 128), color=(40, 80, 120)).save(buf, format="WEBP", quality=85)
    webp = buf.getvalue()

    async with db_session.async_session_factory() as session:
        service = MediaService(session)
        asset = await service.create_from_bytes(webp, "image/webp", original_name="ref.webp")
        await session.commit()
        asset_id = asset.id

    app_settings.auth_enabled = True
    app_settings.trusted_internal_allow_loopback = True
    from app.security.trusted_internal import refresh_trusted_internal_from_settings

    refresh_trusted_internal_from_settings()

    resp = await client.get(f"/media/asset/{asset_id}/llm")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content[:2] == b"\xff\xd8"
