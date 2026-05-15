"""Тесты сборки текста сообщений."""

from __future__ import annotations

from app.services.message_builder import finalize_assistant_text, strip_markdown_images


def test_strip_markdown_images() -> None:
    text = "Вот результат:\n\n![alt](/media/asset/abc)\n\nСпасибо!"
    assert strip_markdown_images(text) == "Вот результат:\n\nСпасибо!"


def test_finalize_assistant_text_rewrites_and_strips() -> None:
    raw = "Готово ![x](/media/generated/old.png)"
    out = finalize_assistant_text(
        raw,
        media_url_rewrites={"/media/generated/old.png": "/media/asset/new-id"},
    )
    assert "![x]" not in out
    assert "/media/asset/new-id" not in out
    assert out == "Готово"
