"""Сборка A1111 infotext."""

from __future__ import annotations

from app.services.sd_infotext import (
    build_a1111_infotext,
    build_infotext_from_fields,
    infotext_from_png_bytes,
)
from app.services.sd_metadata import SdMetadata


def test_build_infotext_from_fields() -> None:
    text = build_infotext_from_fields(
        prompt="a cat",
        negative="blurry",
        params="Steps: 20, Sampler: Euler a, Seed: 1",
    )
    assert "a cat" in text
    assert "Negative prompt: blurry" in text
    assert "Steps:" in text


def test_build_a1111_infotext() -> None:
    meta = SdMetadata(prompt="sunset", negative="ugly", params="Steps: 22, CFG scale: 7")
    text = build_a1111_infotext(meta)
    assert "sunset" in text
    assert "Negative prompt: ugly" in text
    assert "Steps: 22" in text


def test_infotext_from_png_bytes() -> None:
    import io

    from PIL import Image, PngImagePlugin

    raw = "a dog\nNegative prompt: bad\nSteps: 20, Seed: 99"
    img = Image.new("RGB", (1, 1))
    buf = io.BytesIO()
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", raw)
    img.save(buf, format="PNG", pnginfo=meta)
    text = infotext_from_png_bytes(buf.getvalue())
    assert text is not None
    assert "a dog" in text
    assert "Steps:" in text
