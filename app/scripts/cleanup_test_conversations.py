"""
CLI: удалить беседы с заголовком [pytest] (ручная уборка после тестов в браузере).

  python -m app.scripts.cleanup_test_conversations
  python -m app.scripts.cleanup_test_conversations --orphan-default

С тем же эффектом, что pytest_sessionfinish при WEB_CHAT_TEST_CLEANUP_LIVE=1
или WEB_CHAT_TEST_BASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

from app.constants import DEFAULT_CONVERSATION_TITLE
from app.logging_setup import setup_logging
from tests.cleanup import (
    cleanup_live_test_conversations,
    live_cleanup_explicitly_enabled,
    resolve_live_cleanup_base_url,
    should_cleanup_orphan_titles_on_live,
)
from tests.conventions import is_likely_test_orphan_title, is_test_conversation_title

setup_logging()
logger = logging.getLogger(__name__)


async def cleanup_orphan_titles_on_live() -> int:
    """Удалить сироты: «Новая беседа», «t» — только при ORPHANS=1 и WEB_CHAT_TEST_BASE_URL."""
    if not should_cleanup_orphan_titles_on_live():
        if not live_cleanup_explicitly_enabled():
            logger.error(
                "Сироты: задайте WEB_CHAT_TEST_BASE_URL и WEB_CHAT_TEST_CLEANUP_ORPHANS=1",
            )
        else:
            logger.error(
                "Сироты: включите WEB_CHAT_TEST_CLEANUP_ORPHANS=1",
            )
        return 0

    base = resolve_live_cleanup_base_url()
    if not base:
        logger.error("Задайте WEB_CHAT_TEST_BASE_URL")
        return 0

    headers: dict[str, str] = {}
    from os import environ

    api_key = (environ.get("WEB_CHAT_TEST_API_KEY") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key

    deleted = 0
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30.0) as client:
        resp = await client.get("/api/conversations")
        resp.raise_for_status()
        for item in resp.json():
            title = (item.get("title") or "").strip()
            if not is_likely_test_orphan_title(title):
                continue
            conv_id = item.get("id")
            if not conv_id:
                continue
            del_resp = await client.delete(f"/api/conversations/{conv_id}")
            if del_resp.status_code in (200, 204, 404):
                deleted += 1
                logger.info("Удалена тестовая сирота: %s (%s)", title, conv_id)

    return deleted


async def main() -> int:
    parser = argparse.ArgumentParser(description="Очистка тестовых бесед web-chat")
    parser.add_argument(
        "--orphan-default",
        action="store_true",
        help="Удалить сироты (Новая беседа, «t», Переименована и т.п.)",
    )
    args = parser.parse_args()

    live = await cleanup_live_test_conversations()
    logger.info("Удалено бесед [pytest] и зарегистрированных: %d", live)

    if args.orphan_default:
        orphan = await cleanup_orphan_titles_on_live()
        logger.info("Удалено тестовых сирот: %d", orphan)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
