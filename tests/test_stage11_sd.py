"""Этап 11: img2img, upscale, gallery, trusted sources."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.integrations.media_utils import resolve_trusted_generated_source
from app.integrations.sd_tools import get_gallery, img2img
from app.services.gallery_service import list_generated_images

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_resolve_trusted_generated_bare_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gen = tmp_path / "generated"
    gen.mkdir()
    (gen / "sd_test.png").write_bytes(b"x")
    monkeypatch.setattr("app.integrations.media_utils.GENERATED_ROOT", gen)
    path = resolve_trusted_generated_source("sd_test.png")
    assert path.name == "sd_test.png"


def test_resolve_trusted_generated_rejects_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.media_utils.settings.public_base_url",
        "http://192.168.1.10:8090",
    )
    with pytest.raises(ValueError, match="Недопустимый"):
        resolve_trusted_generated_source("https://evil.com/pic.png")


def test_list_generated_images_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gen = tmp_path / "generated"
    gen.mkdir()
    monkeypatch.setattr("app.services.gallery_service.GENERATED_ROOT", gen)
    assert list_generated_images(limit=10) == []


def test_list_generated_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gen = tmp_path / "generated"
    thumbs = gen / "thumbs"
    gen.mkdir()
    thumbs.mkdir()
    (gen / "a.png").write_bytes(b"x")
    monkeypatch.setattr("app.services.gallery_service.GENERATED_ROOT", gen)
    monkeypatch.setattr("app.services.gallery_service.GENERATED_THUMB_ROOT", thumbs)
    items = list_generated_images(limit=10)
    assert len(items) == 1
    assert items[0].filename == "a.png"


@pytest.mark.asyncio
async def test_list_gallery_images_merges_db_and_disk(
    client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Галерея объединяет MediaAsset и файлы на диске без дублей."""
    from app.db import session as db_session
    from app.services.gallery_service import list_gallery_images
    from app.services.media_service import MediaService

    gen = tmp_path / "generated"
    thumbs = gen / "thumbs"
    gen.mkdir()
    thumbs.mkdir()
    (gen / "only_local.png").write_bytes(b"local")
    monkeypatch.setattr("app.services.gallery_service.GENERATED_ROOT", gen)
    monkeypatch.setattr("app.services.gallery_service.GENERATED_THUMB_ROOT", thumbs)

    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        await media.create_from_bytes(MINIMAL_PNG, "image/png", original_name="in_db.png")
        await session.commit()

        items = await list_gallery_images(session, limit=50)
        names = {i.filename for i in items}
        assert "in_db.png" in names
        assert "only_local.png" in names
        assert len(items) == 2
        db_item = next(i for i in items if i.filename == "in_db.png")
        assert db_item.source == "db"
        assert "/media/asset/" in db_item.url


def test_img2img_requires_prompt() -> None:
    with pytest.raises(ValueError, match="prompt"):
        img2img("", init_image_url="x.png", init_image_bytes=b"fake")
