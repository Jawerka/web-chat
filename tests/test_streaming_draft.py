"""Тесты черновика стрима (без lazy-load ORM после commit)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from app.services.streaming_draft import AssistantStreamDraft


def test_content_json_reads_cache_not_orm() -> None:
    """_content_json не обращается к ORM (избегаем MissingGreenlet)."""
    draft = AssistantStreamDraft(uuid.uuid4(), MagicMock())
    draft._json_cache = {"phase": "tool", "images": ["/media/asset/x"]}
    assert draft._content_json() == {"phase": "tool", "images": ["/media/asset/x"]}
