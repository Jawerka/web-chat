"""Тесты очистки по retention."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import settings
from app.db.models import Attachment
from app.integrations import media_utils
from app.services.cleanup_service import (
    cleanup_directory_by_mtime,
    cleanup_stale_attachments,
    run_filesystem_cleanup,
)


@pytest.fixture
def isolated_media_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолированные каталоги uploads/generated."""
    gen = tmp_path / "generated"
    thumbs = gen / "thumbs"
    uploads = tmp_path / "uploads"
    gen.mkdir()
    thumbs.mkdir()
    uploads.mkdir()
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)
    monkeypatch.setattr(media_utils, "GENERATED_THUMB_ROOT", thumbs)
    monkeypatch.setattr(media_utils, "UPLOAD_ROOT", uploads)
    monkeypatch.setattr(settings, "generated_retention_days", 1)
    monkeypatch.setattr(settings, "upload_retention_days", 1)
    return tmp_path


def test_cleanup_directory_removes_old_files(isolated_media_roots: Path) -> None:
    """Файлы старше retention удаляются."""
    old = isolated_media_roots / "generated" / "old.png"
    old.write_bytes(b"x")
    old_time = time.time() - 3 * 86400
    import os

    os.utime(old, (old_time, old_time))
    removed = cleanup_directory_by_mtime(
        isolated_media_roots / "generated",
        settings.generated_retention_days,
    )
    assert removed == 1
    assert not old.exists()


def test_run_filesystem_cleanup_counts(isolated_media_roots: Path) -> None:
    """run_filesystem_cleanup возвращает счётчики."""
    (isolated_media_roots / "generated" / "keep.png").write_bytes(b"1")
    stats = run_filesystem_cleanup()
    assert "generated_files" in stats
    assert "upload_files" in stats


@pytest.mark.asyncio
async def test_cleanup_stale_attachments(
    client: AsyncClient,
    isolated_media_roots: Path,
) -> None:
    """Сироты Attachment без message_id старше retention удаляются."""
    from app.db import session as db_session

    settings.upload_retention_days = 1
    att_id = uuid.uuid4()
    upload_dir = isolated_media_roots / "uploads" / str(att_id)
    upload_dir.mkdir(parents=True)
    (upload_dir / "doc.pdf").write_bytes(b"pdf")

    async with db_session.async_session_factory() as session:
        att = Attachment(
            id=att_id,
            original_name="doc.pdf",
            mime_type="application/pdf",
            size_bytes=3,
            storage_path=f"{att_id}/doc.pdf",
            created_at=datetime.now(UTC) - timedelta(days=10),
        )
        session.add(att)
        await session.flush()
        removed = await cleanup_stale_attachments(session)
        await session.commit()

    assert removed == 1
    assert not upload_dir.exists()
