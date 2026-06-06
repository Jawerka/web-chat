"""Имена файлов SD по шаблону refs/main.py."""

from __future__ import annotations

import hashlib
import io
import time
from datetime import UTC, datetime

from PIL import Image, PngImagePlugin

from app.integrations import media_utils
from app.integrations.sd_filename import (
    build_sd_image_filename,
    extract_seed_from_parameters,
    resolve_upload_display_name,
    save_sd_generated_image,
)


def _png_with_parameters(params: str) -> bytes:
    img = Image.new("RGB", (1, 1), color=(0, 0, 0))
    buf = io.BytesIO()
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", params)
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def test_extract_seed_from_parameters() -> None:
    raw = "Steps: 20, Sampler: Euler, Seed: 424242, Size: 512x512"
    assert extract_seed_from_parameters(raw) == "424242"


def test_build_sd_image_filename_matches_refs_pattern() -> None:
    data = _png_with_parameters("prompt\nNegative prompt: bad\nSteps: 20, Seed: 99")
    at = datetime(2026, 6, 4, 11, 30, tzinfo=UTC)
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        short_hash = hashlib.md5(im.tobytes()).hexdigest()[:5]
    ts = int(at.timestamp()) + 3 * 3600
    date_part = time.strftime("%m-%d %H-%M", time.gmtime(ts))

    name = build_sd_image_filename(
        data,
        mime_type="image/png",
        fallback_name="upload.png",
        created_at=at,
    )
    assert name == f"{date_part} {short_hash} - 99.png"


def test_build_sd_image_filename_seed_override() -> None:
    img = Image.new("RGB", (2, 2), color=(1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()

    name = build_sd_image_filename(
        data,
        mime_type="image/png",
        seed_override=777,
    )
    assert name is not None
    assert name.endswith(" - 777.png")


def test_resolve_upload_display_name_fallback_without_seed() -> None:
    img = Image.new("RGB", (2, 2), color=(1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()

    name = resolve_upload_display_name(
        data,
        mime_type="image/png",
        fallback_name="my photo.png",
    )
    assert name == "myphoto.png"


def test_save_sd_generated_image_uses_refs_pattern(
    tmp_path,
    monkeypatch,
) -> None:
    gen = tmp_path / "generated"
    thumbs = gen / "thumbs"
    gen.mkdir()
    thumbs.mkdir()
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumbs)

    data = _png_with_parameters("Steps: 20, Seed: 424242")
    name, thumb = save_sd_generated_image(data, seed=424242)
    assert " - 424242.png" in name
    assert (gen / name).is_file()
    assert thumb is not None
    assert (thumbs / thumb).is_file()


def test_generated_media_url_encodes_spaces() -> None:
    name = "06-04 12-30 abcde - 99.png"
    url = media_utils.generated_media_url(name)
    assert "%20" in url
    assert media_utils.safe_generated_filename(url.split("/")[-1]) == name
