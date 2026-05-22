"""Тесты черновика стрима (без lazy-load ORM после commit)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.streaming_draft import AssistantStreamDraft


def test_content_json_reads_cache_not_orm() -> None:
    """_content_json не обращается к Message.content_json (избегаем MissingGreenlet)."""
    draft = AssistantStreamDraft(
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )
    draft._json_cache = {"phase": "tool", "images": ["/media/asset/x"]}
    msg = MagicMock()
    msg.content_json = {"phase": "text"}
    draft._message = msg
    assert draft._content_json() == {"phase": "tool", "images": ["/media/asset/x"]}
