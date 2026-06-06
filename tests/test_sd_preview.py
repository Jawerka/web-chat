"""SD live preview data URLs."""

from __future__ import annotations

import pytest

from app.config import settings
from app.integrations.sd_preview import preview_data_url_from_b64


def test_preview_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sd_preview_enabled", False)
    assert preview_data_url_from_b64("abc") is None


def test_preview_jpeg_prefix() -> None:
    assert preview_data_url_from_b64("/9j/abc") == "data:image/jpeg;base64,/9j/abc"
