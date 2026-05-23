"""P2.4: очистка orphan-файлов в data/generated/."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import settings
from app.integrations import media_utils
from app.services.gallery_service import cleanup_orphan_generated_on_disk


@pytest.fixture
def isolated_generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    gen = tmp_path / "generated"
    thumbs = gen / "thumbs"
    gen.mkdir()
    thumbs.mkdir()
    import app.services.gallery_service as gallery_mod

    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumbs)
    monkeypatch.setattr(gallery_mod, "GENERATED_ROOT", gen)
    monkeypatch.setattr(gallery_mod, "GENERATED_THUMB_ROOT", thumbs)
    return gen


@pytest.mark.asyncio
async def test_orphan_cleanup_dry_run_skips_young_files(
    client: AsyncClient,
    isolated_generated: Path,
) -> None:
    orphan = isolated_generated / "orphan.png"
    orphan.write_bytes(b"\x89PNG\r\n\x1a\n")
    from app.db import session as db_session

    async with db_session.async_session_factory() as session:
        stats = await cleanup_orphan_generated_on_disk(
            session,
            dry_run=True,
            min_age_hours=24,
        )
    assert stats["dry_run"] is True
    assert "orphan.png" not in stats["candidates"]


@pytest.mark.asyncio
async def test_orphan_cleanup_deletes_old_unclaimed(
    client: AsyncClient,
    isolated_generated: Path,
) -> None:
    old = isolated_generated / "stale.png"
    old.write_bytes(b"\x89PNG\r\n\x1a\n")
    old_time = time.time() - 48 * 3600
    import os

    os.utime(old, (old_time, old_time))

    from app.db import session as db_session

    async with db_session.async_session_factory() as session:
        stats = await cleanup_orphan_generated_on_disk(
            session,
            dry_run=False,
            min_age_hours=1,
        )
        await session.commit()
    assert stats["deleted"] == 1
    assert not old.exists()


@pytest.mark.asyncio
async def test_orphan_cleanup_api(client: AsyncClient, isolated_generated: Path) -> None:
    old = isolated_generated / "api_orphan.png"
    old.write_bytes(b"x")
    old_time = time.time() - 48 * 3600
    import os

    os.utime(old, (old_time, old_time))

    preview = await client.post("/api/gallery/cleanup-orphans", params={"dry_run": True})
    assert preview.status_code == 200
    body = preview.json()
    assert "api_orphan.png" in body["disk"]["candidates"]

    done = await client.post(
        "/api/gallery/cleanup-orphans",
        params={"dry_run": False, "min_age_hours": 1},
    )
    assert done.status_code == 200
    assert done.json()["disk"]["deleted"] >= 1
    assert not old.exists()
