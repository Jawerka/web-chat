"""Ф1: полный каталог @alias в system prompt."""

from __future__ import annotations

from app.db.models import PromptMacro, PromptMacroCategory
from app.services.prompt_macro_service import (
    append_full_macro_catalog_to_system,
    build_full_macro_catalog_block,
    parse_macro_context_mode,
)


def test_parse_macro_context_mode() -> None:
    assert parse_macro_context_mode(None) == "selected"
    assert parse_macro_context_mode("selected") == "selected"
    assert parse_macro_context_mode("full") == "full"
    assert parse_macro_context_mode("FULL") == "full"
    assert parse_macro_context_mode("semantic") == "semantic"


def _macro(alias: str, body: str, *, label: str = "") -> PromptMacro:
    return PromptMacro(
        category=PromptMacroCategory.CHARACTER,
        alias=alias,
        label=label or alias,
        body=body,
        sort_order=0,
    )


def test_build_catalog_includes_aliases() -> None:
    macros = [
        _macro("pony_a", "body alpha"),
        _macro("pony_b", "body beta"),
    ]
    block = build_full_macro_catalog_block(macros, max_chars=5000, max_macros=10)
    assert "@pony_a" in block
    assert "body alpha" in block
    assert "@pony_b" in block


def test_build_catalog_truncates_by_macro_count() -> None:
    macros = [_macro(f"m{i}", f"text{i}") for i in range(5)]
    block = build_full_macro_catalog_block(macros, max_chars=100_000, max_macros=2)
    assert "@m0" in block
    assert "@m1" in block
    assert "@m4" not in block
    assert "обрезан" in block.lower()


def test_append_to_system_prompt() -> None:
    macros = [_macro("x", "long body")]
    out = append_full_macro_catalog_to_system(
        "Base system.",
        macros,
        max_chars=5000,
        max_macros=10,
    )
    assert out.startswith("Base system.")
    assert "@x" in out
    assert "long body" in out
