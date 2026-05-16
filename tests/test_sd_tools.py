"""Тесты generate_image с mock SD API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.integrations import media_utils
from app.integrations.sd_tools import generate_image

MINIMAL_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def test_generate_image_saves_and_returns_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock SD txt2img → файл в generated и URL в ответе."""
    gen_dir = Path(tmp_path) / "generated"
    thumb_dir = gen_dir / "thumbs"
    gen_dir.mkdir()
    thumb_dir.mkdir()
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen_dir)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumb_dir)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"images": [MINIMAL_PNG_B64]}

    mock_session = MagicMock()
    mock_session.post.return_value = mock_resp

    with patch("app.integrations.sd_tools.get_sd_session", return_value=mock_session):
        result = generate_image("a cat", steps=22, width=768, height=768)

    assert "media/generated/" in result
    assert "Генерация завершена" in result
    files = list(gen_dir.glob("*.png"))
    assert len(files) == 1
