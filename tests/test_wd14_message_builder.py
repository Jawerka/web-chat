"""Формат WD14-тегов в message_builder."""

from __future__ import annotations

from app.services.message_builder import (
    Wd14TagEntry,
    format_wd14_tag_block,
    inject_wd14_for_llm_dict,
)


def test_format_wd14_tag_block() -> None:
    entries = [
        Wd14TagEntry("id1", "photo.png", "1girl, solo"),
        Wd14TagEntry("id2", "bg.jpg", "landscape, sky"),
    ]
    block = format_wd14_tag_block(entries)
    assert block.startswith("[WD14 теги]")
    assert "photo.png: 1girl, solo" in block
    assert "bg.jpg: landscape, sky" in block


def test_format_wd14_tag_block_empty_tags() -> None:
    entries = [Wd14TagEntry("id1", "x.png", "")]
    assert format_wd14_tag_block(entries) == ""


def test_inject_wd14_for_llm_dict_prepends_to_text() -> None:
    parts = [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "http://x/a"}},
    ]
    cj = {
        "wd14": [{"attachment_id": "a", "filename": "a.png", "tags": "cat, solo"}],
    }
    out = inject_wd14_for_llm_dict(parts, cj)
    assert out[0]["type"] == "text"
    assert "[WD14 теги]" in out[0]["text"]
    assert "a.png: cat, solo" in out[0]["text"]
    assert "hello" in out[0]["text"]


def test_inject_wd14_for_llm_dict_no_wd14() -> None:
    parts = [{"type": "text", "text": "hello"}]
    assert inject_wd14_for_llm_dict(parts, {}) == parts
