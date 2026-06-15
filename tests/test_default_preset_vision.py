"""Дефолтный пресет: vision для вложений, без SD-tools."""

from __future__ import annotations

from app.db.seed import DEFAULT_PROMPT


def test_default_prompt_mentions_image_vision() -> None:
    lower = DEFAULT_PROMPT.lower()
    assert "изображен" in lower
    assert "vision" in lower
    assert "extract_text" in lower
