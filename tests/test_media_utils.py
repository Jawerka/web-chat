"""Unit-тесты media_utils."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.integrations.media_utils import resolve_upload_file, safe_filename


def test_safe_filename_normal() -> None:
    assert safe_filename("photo_01.png") == "photo_01.png"


def test_safe_filename_empty_after_sanitize() -> None:
    assert safe_filename("@#$%") == ""


def test_resolve_upload_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_upload_file бросает FileNotFoundError, если файла нет."""
    import app.integrations.media_utils as media_utils

    monkeypatch.setattr(media_utils, "UPLOAD_ROOT", tmp_path)
    aid = uuid.uuid4()
    (tmp_path / str(aid)).mkdir()
    with pytest.raises(FileNotFoundError):
        resolve_upload_file(aid, "missing.png")
