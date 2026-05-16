"""Тесты фрагментов для поиска."""

from __future__ import annotations

from app.services.search_snippet import build_search_snippet, search_tokens


def test_search_tokens_splits_words() -> None:
    assert search_tokens("Queen Chrysalis") == ["Queen", "Chrysalis"]
    assert search_tokens("токены") == ["токены"]


def test_snippet_around_match() -> None:
    before = "A" * 60
    after = "B" * 60
    text = f"{before}совпадением{after}"
    snippet = build_search_snippet(text, "совпадением")
    assert "совпадением" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")
    assert len(snippet) <= 50 + len("совпадением") + 50 + 2


def test_snippet_first_matching_word() -> None:
    text = "alpha beta gamma"
    snippet = build_search_snippet(text, "gamma beta")
    assert "gamma" in snippet or "beta" in snippet


def test_snippet_without_match() -> None:
    snippet = build_search_snippet("простой текст", "нет")
    assert snippet.startswith("простой")
