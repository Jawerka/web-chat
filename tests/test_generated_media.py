"""Тесты сохранения и раздачи generated изображений."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.integrations import media_utils


def test_save_image_and_thumbnail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save_image_from_base64 + make_thumbnail создают файлы."""
    gen = tmp_path / "gen"
    thumbs = gen / "thumbs"
    gen.mkdir()
    thumbs.mkdir()
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumbs)

    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    name = media_utils.save_image(png)
    thumb = media_utils.make_thumbnail(name)
    assert (gen / name).is_file()
    assert thumb is not None
    assert (thumbs / thumb).is_file()


@pytest.mark.asyncio
async def test_serve_generated_file(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /media/generated/{filename} отдаёт сохранённый файл."""
    gen_path = tmp_path / "generated"
    thumb_path = gen_path / "thumbs"
    gen_path.mkdir()
    thumb_path.mkdir()
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen_path)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumb_path)

    name = media_utils.save_image(b"fake-png-content", "test_gen.png")
    response = await client.get(f"/media/generated/{name}")
    assert response.status_code == 200
