"""Тесты сборки текста сообщений."""

from __future__ import annotations

from app.services.message_builder import (
    finalize_assistant_text,
    is_img2img_gen_preset_instruction_block,
    strip_img2img_gen_preset_prefix,
    strip_llm_image_context_note,
    strip_markdown_images,
)


def test_strip_markdown_images() -> None:
    text = "Вот результат:\n\n![alt](/media/asset/abc)\n\nСпасибо!"
    assert strip_markdown_images(text) == "Вот результат:\n\nСпасибо!"


def test_img2img_gen_preset_instruction_block() -> None:
    block = "denoising 0.40-0.50; CFG 5.0-7.0; Сделай 10 изображений."
    assert is_img2img_gen_preset_instruction_block(block)


def test_strip_img2img_gen_preset_prefix_with_prompt() -> None:
    raw = "denoising 0.40-0.50; CFG 5.0-7.0; Сделай 10 изображений.\n\n@rainbow_dash"
    assert strip_img2img_gen_preset_prefix(raw) == "@rainbow_dash"


def test_strip_img2img_gen_preset_prefix_only_hint() -> None:
    raw = "denoising 0.40-0.50; CFG 5.0-7.0; Сделай 10 изображений."
    assert strip_img2img_gen_preset_prefix(raw) == ""


def test_strip_llm_image_context_note_old_and_new() -> None:
    old = (
        "Готово.\n\n[В этом ответе были изображения (для контекста): "
        "http://192.168.1.1/media/asset/abc/llm]"
    )
    assert strip_llm_image_context_note(old) == "Готово."
    new = (
        "Ок.\n\n[CTX generated_images: d1108e9e-75a9-4a8c-8542-b5b30f00a583 | "
        "служебная пометка для контекста, не цитируй пользователю]"
    )
    assert strip_llm_image_context_note(new) == "Ок."


def test_finalize_assistant_text_strips_echoed_context_note() -> None:
    raw = (
        "Вот результат.\n\n[В этом ответе были изображения (для контекста): "
        "http://x/media/asset/u/llm]"
    )
    assert finalize_assistant_text(raw) == "Вот результат."


def test_finalize_assistant_text_rewrites_and_strips() -> None:
    raw = "Готово ![x](/media/generated/old.png)"
    out = finalize_assistant_text(
        raw,
        media_url_rewrites={"/media/generated/old.png": "/media/asset/new-id"},
    )
    assert "![x]" not in out
    assert "/media/asset/new-id" not in out
    assert out == "Готово"
