"""Фикстуры pytest: изолированная SQLite на каждый тест."""

from __future__ import annotations

import os

# До импорта app — не писать pytest-прогоны в production logs/web-chat.log
os.environ.setdefault("WEB_CHAT_DISABLE_LOG_FILE", "1")

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import dispose_database, init_db
from tests.safety import safe_configure_database
from app.main import create_app
from tests.cleanup import run_session_cleanup
from tests.conventions import format_test_conversation_title
from tests.safety import assert_not_using_production_database


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: тесты против WEB_CHAT_TEST_BASE_URL; очистка только [pytest] на live",
    )
    config.addinivalue_line(
        "markers",
        "load: опциональные integration/load-тесты (WS, параллельность, SD tools); pytest -m 'not load' для быстрого прогона",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """После всех тестов: удалить беседы с префиксом [pytest] (не во время тестов)."""
    run_session_cleanup()


@pytest.fixture
def test_conv_title(request: pytest.FixtureRequest) -> str:
    """Единообразный заголовок беседы для текущего теста (префикс [pytest])."""
    return format_test_conversation_title(request.node.nodeid)


@pytest.fixture
def repo_conv_title(test_conv_title: str) -> str:
    """Тот же заголовок для ConversationRepository.create в unit-тестах."""
    return test_conv_title


@pytest.fixture
async def test_conversation(
    client: AsyncClient,
    test_conv_title: str,
) -> AsyncIterator[dict[str, Any]]:
    """
    Создать беседу с тестовым заголовком; удаление — в pytest_sessionfinish.

    Для тестов, которые сами удаляют беседу, используйте api_create_conversation().
    """
    from tests.helpers import api_create_conversation

    data = await api_create_conversation(client, test_conv_title)
    yield data
    # Намеренно не удаляем здесь — только финишер сессии


@pytest.fixture(autouse=True)
def _test_security_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """В тестах по умолчанию без API key, auth и с мягким rate limit."""
    from app.config import settings
    from app.security.rate_limit import reset_rate_limits_for_tests

    monkeypatch.setattr(settings, "api_access_key", "")
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "multi_user_enabled", False)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    reset_rate_limits_for_tests()
    yield
    reset_rate_limits_for_tests()


@pytest.fixture(autouse=True)
def _track_created_conversations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Регистрировать id всех бесед с нестандартным заголовком (включая «t»)."""
    from app.db.repositories import ConversationRepository
    from tests.cleanup import record_test_conversation_id
    from tests.conventions import should_register_test_conversation

    original_create = ConversationRepository.create

    async def create_with_tracking(
        self,
        *,
        title: str,
        preset_id,
        owner_user_id=None,
    ):
        conversation = await original_create(
            self,
            title=title,
            preset_id=preset_id,
            owner_user_id=owner_user_id,
        )
        if should_register_test_conversation(title):
            record_test_conversation_id(conversation.id)
        return conversation

    monkeypatch.setattr(ConversationRepository, "create", create_with_tracking)


@pytest.fixture(autouse=True)
def _disable_wd_tagger_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не поднимать WD14 worker в unit-тестах."""
    from unittest.mock import AsyncMock

    from app.config import settings

    monkeypatch.setattr(settings, "wd_tagger_enabled", False)
    monkeypatch.setattr(
        "app.integrations.wd_tagger_service.wd_tagger_service.start",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.integrations.wd_tagger_service.wd_tagger_service.stop",
        AsyncMock(),
    )
    monkeypatch.setattr("app.main.wd_tagger_service.start", AsyncMock())
    monkeypatch.setattr("app.main.wd_tagger_service.stop", AsyncMock())


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
    await dispose_database()
    db_file = tmp_path / "test.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    safe_configure_database(db_url)
    await init_db()
    assert_not_using_production_database()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await dispose_database()
