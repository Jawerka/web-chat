"""
Соглашения для тестовых данных в web-chat.

Беседы (вкладки в UI), созданные в тестах, должны иметь заголовок с префиксом
TEST_CONVERSATION_PREFIX — тогда pytest_sessionfinish удалит только их.
"""

from __future__ import annotations

import re
import uuid

from app.constants import DEFAULT_CONVERSATION_TITLE

# Единый префикс: виден в сайдбаре, безопасен для LIKE/startswith.
# Все тесты создают беседы через tests.helpers.api_create_conversation / sync_api_create_conversation.
TEST_CONVERSATION_PREFIX = "[pytest]"

# Строгий шаблон для «лишних» бесед, созданных не через helper
TEST_CONVERSATION_TITLE_RE = re.compile(
    r"^\[pytest\]\s+.+",
    re.IGNORECASE,
)


def is_test_conversation_title(title: str | None) -> bool:
    """Заголовок создан тестом и подлежит финальной очистке."""
    if not title:
        return False
    return title.startswith(TEST_CONVERSATION_PREFIX)


# Заголовки из тестов без префикса [pytest] (PATCH, старые create(title="t"))
KNOWN_TEST_ORPHAN_TITLES = frozenset(
    {
        "Переименована",
    }
)


def should_register_test_conversation(title: str | None) -> bool:
    """Автотрекинг в conftest: только заголовки с префиксом [pytest]."""
    return is_test_conversation_title(title)


def is_likely_test_orphan_title(title: str | None) -> bool:
    """Сирота после pytest: короткий или известный тестовый заголовок без [pytest]."""
    if not title or is_test_conversation_title(title):
        return False
    stripped = title.strip()
    if stripped == DEFAULT_CONVERSATION_TITLE:
        return True
    if stripped in KNOWN_TEST_ORPHAN_TITLES:
        return True
    # title="t" и подобные однобуквенные заголовки из unit-тестов
    return len(stripped) == 1


def format_test_conversation_title(
    nodeid: str,
    *,
    suffix: str | None = None,
) -> str:
    """
    Заголовок беседы для текущего теста.

    Пример: ``[pytest] test_conversations › test_list_and_get``.
    """
    short = nodeid
    if "tests/" in short:
        short = short.split("tests/", 1)[-1]
    short = short.replace("::", " › ").replace(".py", "")
    title = f"{TEST_CONVERSATION_PREFIX} {short}"
    if suffix:
        title = f"{title} ({suffix})"
    else:
        title = f"{title} [{uuid.uuid4().hex[:8]}]"
    return title[:200]
