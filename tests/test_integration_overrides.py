"""Парсинг WS integration overrides."""

from __future__ import annotations

from app.integrations.runtime_config import parse_integration_overrides


def test_parse_document_rag_flag() -> None:
    off = parse_integration_overrides({"document_rag": False})
    assert off.document_rag is False

    on = parse_integration_overrides({"document_rag": True})
    assert on.document_rag is True

    on_str = parse_integration_overrides({"document_rag": "true"})
    assert on_str.document_rag is True
