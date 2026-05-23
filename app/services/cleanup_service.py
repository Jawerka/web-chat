"""
Очистка устаревших файлов и записей вложений по retention из config.
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Attachment
from app.integrations import media_utils

logger = logging.getLogger(__name__)


def _cutoff_timestamp(days: int) -> float:
    """Unix timestamp границы: файлы старше days удаляются."""
    return time.time() - days * 86400


def cleanup_directory_by_mtime(root: Path, retention_days: int) -> int:
    """
    Удалить файлы в каталоге (рекурсивно), старше retention_days по mtime.

    Returns:
        Число удалённых файлов.
    """
    if retention_days <= 0 or not root.is_dir():
        return 0
    cutoff = _cutoff_timestamp(retention_days)
    removed = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Не удалось удалить %s: %s", path, exc)
    _prune_empty_dirs(root)
    return removed


def _prune_empty_dirs(root: Path) -> None:
    """Удалить пустые подкаталоги (снизу вверх)."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def run_filesystem_cleanup() -> dict[str, int]:
    """
    Очистить data/generated/ и data/uploads/ по настройкам retention.

    Returns:
        Счётчики удалённых файлов по категориям.
    """
    generated = cleanup_directory_by_mtime(
        media_utils.GENERATED_ROOT,
        settings.generated_retention_days,
    )
    generated += cleanup_directory_by_mtime(
        media_utils.GENERATED_THUMB_ROOT,
        settings.generated_retention_days,
    )
    uploads = cleanup_directory_by_mtime(
        media_utils.UPLOAD_ROOT,
        settings.upload_retention_days,
    )
    total = generated + uploads
    if total:
        logger.info(
            "Очистка файлов: generated=%d, uploads=%d (retention %d/%d дн.)",
            generated,
            uploads,
            settings.generated_retention_days,
            settings.upload_retention_days,
        )
    return {"generated_files": generated, "upload_files": uploads}


async def cleanup_stale_attachments(session: AsyncSession) -> int:
    """
    Удалить записи Attachment без message_id, старше upload_retention_days.

    Удаляет каталоги на диске data/uploads/{id}/.
    """
    days = settings.upload_retention_days
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        select(Attachment).where(
            Attachment.message_id.is_(None),
            Attachment.created_at < cutoff,
        )
    )
    rows = list(result.scalars().all())
    removed = 0
    for att in rows:
        upload_dir = media_utils.UPLOAD_ROOT / str(att.id)
        if upload_dir.is_dir():
            shutil.rmtree(upload_dir, ignore_errors=True)
        await session.delete(att)
        removed += 1
    if removed:
        await session.flush()
        logger.info("Удалено устаревших вложений (БД): %d", removed)
    return removed


async def run_full_cleanup(session: AsyncSession) -> dict[str, int]:
    """Полная очистка: файлы на диске + сироты вложений в БД + orphan generated."""
    from app.services.gallery_service import cleanup_orphan_generated_on_disk

    stats = run_filesystem_cleanup()
    stats["attachments_db"] = await cleanup_stale_attachments(session)
    orphan = await cleanup_orphan_generated_on_disk(session, dry_run=False)
    stats["orphan_generated"] = int(orphan.get("deleted", 0))
    return stats
