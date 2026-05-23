"""Статусы прогресса для UI."""

from __future__ import annotations

from app.services.user_progress import (
    STAGE_SD_RENDER,
    build_progress,
    progress_from_sd_snapshot,
    stage_for_tool,
)


def test_stage_for_sd_tools() -> None:
    assert stage_for_tool("generate_image") == STAGE_SD_RENDER
    assert stage_for_tool("upscale_images") == "sd_upscale"


def test_build_progress_includes_percent_in_label() -> None:
    payload = build_progress(STAGE_SD_RENDER, tool="generate_image", percent=42)
    assert payload["percent"] == 42
    assert "42%" in payload["label"]


def test_progress_from_sd_snapshot() -> None:
    payload = progress_from_sd_snapshot(
        "img2img",
        {"percent": 55, "detail": "шаг 5/22"},
    )
    assert payload["stage"] == STAGE_SD_RENDER
    assert payload["percent"] == 55
    assert "шаг" in payload["detail"]
