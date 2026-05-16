"""Фильтрация tools по slug пресета."""

from __future__ import annotations

from app.integrations.tool_definitions import tools_for_preset_slug


def test_image_gen_preset_excludes_img2img() -> None:
    names = {t["function"]["name"] for t in tools_for_preset_slug("image_gen")}
    assert "generate_image" in names
    assert "img2img" not in names


def test_img2img_preset_excludes_generate_image() -> None:
    names = {t["function"]["name"] for t in tools_for_preset_slug("img2img")}
    assert "img2img" in names
    assert "generate_image" not in names


def test_default_preset_has_all_tools() -> None:
    names = {t["function"]["name"] for t in tools_for_preset_slug("default")}
    assert "generate_image" in names
    assert "img2img" in names
    assert "extract_text" in names
