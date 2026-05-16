"""API галереи: список и удаление."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.integrations import media_utils

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_gallery_lists_db_and_disk(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gen = tmp_path / "generated"
    thumbs = gen / "thumbs"
    gen.mkdir()
    thumbs.mkdir()
    (gen / "local_only.png").write_bytes(b"x")
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumbs)
    import app.services.gallery_service as gallery_service

    monkeypatch.setattr(gallery_service, "GENERATED_ROOT", gen)
    monkeypatch.setattr(gallery_service, "GENERATED_THUMB_ROOT", thumbs)

    from app.db import session as db_session
    from app.services.media_service import MediaService

    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        await media.create_from_bytes(MINIMAL_PNG, "image/png", original_name="db_one.png")
        await session.commit()

    r = await client.get("/api/gallery?limit=50")
    assert r.status_code == 200
    data = r.json()
    names = {i["filename"] for i in data["images"]}
    assert "db_one.png" in names
    assert "local_only.png" in names
    assert all("id" in i for i in data["images"])


@pytest.mark.asyncio
async def test_delete_gallery_asset(client: AsyncClient) -> None:
    from app.db import session as db_session
    from app.services.media_service import MediaService

    async with db_session.async_session_factory() as session:
        media = MediaService(session)
        asset = await media.create_from_bytes(MINIMAL_PNG, "image/png", original_name="del.png")
        await session.commit()
        aid = asset.id

    r = await client.delete(f"/api/gallery/db/{aid}")
    assert r.status_code == 204

    r2 = await client.get("/api/gallery?limit=50")
    ids = {i["id"] for i in r2.json()["images"]}
    assert str(aid) not in ids


@pytest.mark.asyncio
async def test_delete_gallery_disk(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gen = tmp_path / "generated"
    thumbs = gen / "thumbs"
    gen.mkdir()
    thumbs.mkdir()
    (gen / "gone.png").write_bytes(b"x")
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumbs)
    import app.services.gallery_service as gallery_service

    monkeypatch.setattr(gallery_service, "GENERATED_ROOT", gen)
    monkeypatch.setattr(gallery_service, "GENERATED_THUMB_ROOT", thumbs)

    r = await client.delete("/api/gallery/disk/gone.png")
    assert r.status_code == 204
    assert not (gen / "gone.png").exists()
