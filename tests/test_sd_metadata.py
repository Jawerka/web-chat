"""Парсинг SD parameters."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image, PngImagePlugin

from app.services.sd_metadata import extract_sd_metadata_from_bytes


def _png_with_parameters(params: str) -> bytes:
    img = Image.new("RGB", (1, 1), color=(0, 0, 0))
    buf = io.BytesIO()
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", params)
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def test_extract_sd_metadata() -> None:
    raw = (
        "a cat\n"
        "Negative prompt: blurry\n"
        "Steps: 20, Sampler: Euler, Seed: 1"
    )
    data = _png_with_parameters(raw)
    meta = extract_sd_metadata_from_bytes(data)
    assert meta is not None
    assert "cat" in meta.prompt
    assert "blurry" in meta.negative
    assert "Steps:" in meta.params
