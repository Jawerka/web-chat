"""Фикстуры pytest: изолированная SQLite на каждый тест."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import configure_database, init_db
from app.main import create_app


@pytest.fixture(autouse=True)
def _disable_mcp_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не поднимать MCP-порт и retention loop в unit-тестах."""
    noop = MagicMock()
    stop = MagicMock()
    monkeypatch.setattr("app.integrations.mcp_server.start_mcp_background", lambda: noop)
    monkeypatch.setattr("app.main.start_mcp_background", lambda: noop)
    retention_task = AsyncMock()
    retention_task.cancel = MagicMock()
    monkeypatch.setattr(
        "app.main.start_retention_background",
        lambda: (retention_task, stop),
    )


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент с приложением и временной БД."""
    db_file = tmp_path / "test.sqlite"
    configure_database(f"sqlite+aiosqlite:///{db_file}")
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
