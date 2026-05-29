"""Per-tool timeout_seconds в TOOL_DEFINITIONS (P4.1)."""

from __future__ import annotations

from app.config import settings
from app.integrations.tool_definitions import (
    TOOL_DEFINITIONS,
    _GALLERY_LIST_TIMEOUT_SEC,
    tool_timeout_seconds,
)


def test_all_tools_declare_timeout_seconds() -> None:
    for entry in TOOL_DEFINITIONS:
        assert "timeout_seconds" in entry["function"]


def test_sd_tools_match_request_timeout() -> None:
    for name in ("generate_image", "img2img", "upscale_images"):
        assert tool_timeout_seconds(name) == settings.request_timeout


def test_extract_text_matches_extract_timeout() -> None:
    assert tool_timeout_seconds("extract_text") == settings.extract_timeout_sec


def test_get_gallery_has_bounded_timeout() -> None:
    assert tool_timeout_seconds("get_gallery") == _GALLERY_LIST_TIMEOUT_SEC
