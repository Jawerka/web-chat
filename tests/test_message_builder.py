"""Тесты сборки текста сообщений."""

from __future__ import annotations

from app.services.message_builder import (
    canonical_stored_image_urls,
    finalize_assistant_text,
    is_img2img_gen_preset_instruction_block,
    rewrite_media_urls_in_text,
    strip_img2img_gen_preset_prefix,
    strip_legacy_thumb_urls_from_text,
    strip_llm_image_context_note,
    strip_markdown_images,
)
from app.services.media_service import _generated_url_variants


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


def test_canonical_stored_image_urls_prefers_assets() -> None:
    urls = canonical_stored_image_urls(
        [
            "/media/generated/sd_dead.png",
            "http://lan/media/generated/thumbs/sd_dead.webp",
        ],
        ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
    )
    assert urls == ["/media/asset/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]
    assert all("/generated/" not in u for u in urls)


def test_canonical_stored_image_urls_filters_generated_without_assets() -> None:
    urls = canonical_stored_image_urls(
        ["/media/generated/sd_x.png", "/media/asset/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"],
        [],
    )
    assert urls == ["/media/asset/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]


def test_strip_legacy_thumb_urls_from_text() -> None:
    raw = (
        "Смотрите /media/generated/thumbs/sd_abc.webp\n"
        "Thumbnail: http://192.168.1.1:8090/media/generated/thumbs/sd_abc.webp\n"
        "Готово."
    )
    assert strip_legacy_thumb_urls_from_text(raw) == "Смотрите\n\nГотово."


def test_generated_url_variants_include_thumbs() -> None:
    variants = _generated_url_variants("sd_abc123.png")
    assert "/media/generated/thumbs/sd_abc123.webp" in variants
    assert any("Thumbnail:" in v for v in variants)


def test_rewrite_media_urls_in_text_thumb_line() -> None:
    old = "Thumbnail: http://lan/media/generated/thumbs/sd_x.webp"
    new_url = "/media/asset/11111111-1111-1111-1111-111111111111"
    out = rewrite_media_urls_in_text(
        old,
        {"http://lan/media/generated/thumbs/sd_x.webp": new_url},
    )
    assert new_url in out
    assert "thumbs" not in out


def test_finalize_assistant_text_rewrites_and_strips() -> None:
    raw = "Готово ![x](/media/generated/old.png)"
    out = finalize_assistant_text(
        raw,
        media_url_rewrites={"/media/generated/old.png": "/media/asset/new-id"},
    )
    assert "![x]" not in out
    assert "/media/asset/new-id" not in out
    assert out == "Готово"
