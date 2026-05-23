"""Хелперы для создания тестовых бесед с префиксом [pytest]."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from starlette.testclient import TestClient

import uuid

from app.db.repositories import ConversationRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.cleanup import record_test_conversation_id
from tests.conventions import TEST_CONVERSATION_PREFIX, format_test_conversation_title


def conversation_create_body(
    test_conv_title: str,
    **extra: Any,
) -> dict[str, Any]:
    """Тело POST /api/conversations с обязательным тестовым заголовком."""
    body: dict[str, Any] = {"title": test_conv_title}
    body.update(extra)
    return body


async def api_create_conversation(
    client: AsyncClient,
    test_conv_title: str,
    **extra: Any,
) -> dict[str, Any]:
    """Создать беседу через REST (async)."""
    response = await client.post(
        "/api/conversations",
        json=conversation_create_body(test_conv_title, **extra),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    record_test_conversation_id(data["id"])
    return data


def sync_api_create_conversation(
    client: TestClient,
    test_conv_title: str,
    **extra: Any,
) -> dict[str, Any]:
    """Создать беседу через REST (sync TestClient / WebSocket-тесты)."""
    response = client.post(
        "/api/conversations",
        json=conversation_create_body(test_conv_title, **extra),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    record_test_conversation_id(data["id"])
    return data


def record_created_conversation(data: dict[str, Any]) -> dict[str, Any]:
    """Зарегистрировать id после POST /api/conversations вне helpers."""
    if "id" in data:
        record_test_conversation_id(data["id"])
    return data


async def repo_create_conversation(
    session: AsyncSession,
    preset_id: uuid.UUID,
    test_conv_title: str,
) -> object:
    """ConversationRepository.create с обязательным [pytest] заголовком."""
    conv = await ConversationRepository(session).create(
        title=test_conv_title,
        preset_id=preset_id,
    )
    record_test_conversation_id(conv.id)
    return conv


def repo_test_title(request_nodeid: str) -> str:
    """Заголовок для ConversationRepository.create в unit-тестах."""
    return format_test_conversation_title(request_nodeid)


def is_pytest_conversation_title(title: str | None) -> bool:
    """Алиас для is_test_conversation_title."""
    from tests.conventions import is_test_conversation_title

    return is_test_conversation_title(title)


__all__ = [
    "TEST_CONVERSATION_PREFIX",
    "api_create_conversation",
    "record_created_conversation",
    "repo_create_conversation",
    "repo_test_title",
    "sync_api_create_conversation",
    "conversation_create_body",
]
