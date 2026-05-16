"""Unit-тесты сервиса img2img."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from app.integrations.img2img_service import (
    Img2ImgRequest,
    build_img2img_payload,
    encode_init_image_b64,
    prepare_init_image,
    resolve_output_dimensions,
    sanitize_llm_dimension,
    validate_img2img_request,
)

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _make_png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color=(128, 64, 32)).save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_init_image_keeps_small_size() -> None:
    raw = _make_png(640, 480)
    prepared = prepare_init_image(raw, source_name="ref.png")
    assert prepared.width == 640
    assert prepared.height == 480
    assert prepared.source_name == "ref.png"
    assert prepared.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_prepare_init_image_downscales_large() -> None:
    raw = _make_png(3000, 2000)
    prepared = prepare_init_image(raw, max_side=2048)
    assert max(prepared.width, prepared.height) <= 2048


def test_resolve_output_dimensions_auto() -> None:
    w, h = resolve_output_dimensions(0, 0, 777, 555)
    assert w == 776  # aligned to 8
    assert h == 552


def test_resolve_output_dimensions_explicit() -> None:
    w, h = resolve_output_dimensions(1024, 768, 100, 100)
    assert w == 1024
    assert h == 768


def test_sanitize_llm_dimension_auto_and_clamp() -> None:
    assert sanitize_llm_dimension(0) == 0
    assert sanitize_llm_dimension(-1) == 0
    assert sanitize_llm_dimension(400) == 512
    assert sanitize_llm_dimension(1024) == 1024
    assert sanitize_llm_dimension(3000) == 2048
    assert sanitize_llm_dimension("768") == 768


def test_validate_accepts_resolved_output_size() -> None:
    req = Img2ImgRequest(
        prompt="test",
        init_image_bytes=b"x",
        width=512,
        height=776,
    )
    validate_img2img_request(req)


def test_validate_rejects_empty_prompt() -> None:
    req = Img2ImgRequest(prompt="  ", init_image_bytes=b"x")
    with pytest.raises(ValueError, match="prompt"):
        validate_img2img_request(req)


def test_build_payload_has_init_images() -> None:
    req = Img2ImgRequest(
        prompt="cat",
        init_image_bytes=b"x",
        denoising_strength=0.5,
    )
    b64 = encode_init_image_b64(MINIMAL_PNG)
    payload = build_img2img_payload(req, init_b64=b64, width=512, height=512, seed=42)
    assert payload["init_images"] == [b64]
    assert payload["denoising_strength"] == 0.5
    assert payload["batch_size"] == 1
    assert payload["send_images"] is True
    assert payload["save_images"] is False


def test_parse_upload_from_url() -> None:
    from app.integrations.media_utils import parse_upload_from_url

    att_id = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    parsed = parse_upload_from_url(
        f"http://192.168.1.1:8090/media/uploads/{att_id}/photo.png"
    )
    assert parsed is not None
    assert str(parsed[0]) == att_id
    assert parsed[1] == "photo.png"
