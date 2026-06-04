#!/usr/bin/env python3
"""
Пакетное шифрование MediaAsset с encryption_version=0 (legacy plaintext → AES-GCM).

Пример:
  python -m app.scripts.reencrypt_gallery_assets --batch 50
  python -m app.scripts.reencrypt_gallery_assets --user-id <uuid> --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from dotenv import load_dotenv

load_dotenv()

from app.db.session import async_session_factory, init_db  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.services.gallery_reencrypt_service import reencrypt_plaintext_batch  # noqa: E402

logger = logging.getLogger(__name__)


async def _run(
    *,
    batch: int,
    user_id: uuid.UUID | None,
) -> None:
    await init_db()
    async with async_session_factory() as session:
        total_reencrypted = 0
        total_skipped = 0
        while True:
            stats = await reencrypt_plaintext_batch(
                session,
                owner_user_id=user_id,
                limit=batch,
            )
            await session.commit()
            total_reencrypted += stats["reencrypted"]
            total_skipped += stats["skipped"]
            if stats["candidates"] == 0:
                break
            if stats["reencrypted"] == 0:
                logger.warning(
                    "остановка: %d кандидатов, 0 перешифровано (нет media_token?)",
                    stats["candidates"],
                )
                break
            logger.info(
                "batch: +%d reencrypted, skipped=%d",
                stats["reencrypted"],
                stats["skipped"],
            )
        logger.info(
            "готово: reencrypted=%d skipped=%d",
            total_reencrypted,
            total_skipped,
        )


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Re-encrypt plaintext gallery MediaAssets")
    parser.add_argument("--batch", type=int, default=50, help="размер пакета")
    parser.add_argument("--user-id", type=str, default="", help="только активы владельца")
    args = parser.parse_args()
    uid = uuid.UUID(args.user_id) if args.user_id.strip() else None
    asyncio.run(_run(batch=max(1, args.batch), user_id=uid))


if __name__ == "__main__":
    main()
