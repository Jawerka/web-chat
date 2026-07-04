"""Тесты объединения параллельных img2img tool_calls."""

from __future__ import annotations

from app.services.img2img_tool_coalesce import (
    group_tool_call_batches,
    merge_img2img_args,
)


def _tc(call_id: str) -> dict:
    return {"id": call_id, "function": {"name": "img2img", "arguments": "{}"}}


def test_merge_img2img_args_combines_denoise() -> None:
    merged = merge_img2img_args(
        [
            {"prompt": "cat", "attachment_id": "a", "denoising_strength": 0.74},
            {"prompt": "cat", "attachment_id": "a", "denoising_strength": 0.82},
            {"prompt": "cat", "attachment_id": "a", "denoising_strength": 0.90},
        ]
    )
    assert merged["prompt"] == "cat"
    assert merged["attachment_id"] == "a"
    assert "denoising_strength" not in merged
    assert merged["denoising_strengths"] == [0.74, 0.82, 0.9]


def test_group_coalesces_same_init_and_prompt() -> None:
    uid = "fbe2b769-1687-41b1-adab-35a8ecb073a1"
    parsed = [
        (
            _tc("1"),
            "img2img",
            {"prompt": "tags", "denoising_strength": 0.74, "attachment_id": uid},
        ),
        (
            _tc("2"),
            "img2img",
            {"prompt": "tags", "denoising_strength": 0.80, "attachment_id": uid},
        ),
        (
            _tc("3"),
            "img2img",
            {"prompt": "tags", "denoising_strength": 0.86, "attachment_id": uid},
        ),
    ]
    batches = group_tool_call_batches(parsed)
    assert len(batches) == 1
    assert batches[0].coalesced
    assert len(batches[0].entries) == 3
    assert batches[0].execution_args()["denoising_strengths"] == [0.74, 0.8, 0.86]


def test_group_keeps_different_prompts_separate() -> None:
    uid = "fbe2b769-1687-41b1-adab-35a8ecb073a1"
    parsed = [
        (_tc("1"), "img2img", {"prompt": "a", "denoising_strength": 0.5, "attachment_id": uid}),
        (_tc("2"), "img2img", {"prompt": "b", "denoising_strength": 0.6, "attachment_id": uid}),
    ]
    batches = group_tool_call_batches(parsed)
    assert len(batches) == 2
    assert not batches[0].coalesced
    assert not batches[1].coalesced


def test_group_url_init_matches_attachment_id() -> None:
    uid = "fbe2b769-1687-41b1-adab-35a8ecb073a1"
    parsed = [
        (
            _tc("1"),
            "img2img",
            {
                "prompt": "tags",
                "denoising_strength": 0.5,
                "init_image_url": f"/media/asset/{uid}",
            },
        ),
        (
            _tc("2"),
            "img2img",
            {"prompt": "tags", "denoising_strength": 0.6, "attachment_id": uid},
        ),
    ]
    batches = group_tool_call_batches(parsed)
    assert len(batches) == 1
    assert batches[0].execution_args()["denoising_strengths"] == [0.5, 0.6]
