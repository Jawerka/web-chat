"""
CLI: однократная очистка по retention (для systemd timer).
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.db import session as db_session
from app.db.session import configure_database, init_db
from app.services.cleanup_service import run_full_cleanup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> int:
    """Выполнить очистку и вывести статистику."""
    configure_database(settings.database_url)
    await init_db()
    async with db_session.async_session_factory() as session:
        stats = await run_full_cleanup(session)
        await session.commit()
    logger.info("Cleanup завершён: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
