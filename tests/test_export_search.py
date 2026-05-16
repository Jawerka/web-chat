"""Тесты экспорта беседы и поиска по истории."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.db import session as db_session
from app.db.models import MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository


@pytest.mark.asyncio
async def test_export_conversation_markdown(client: AsyncClient) -> None:
    """GET export возвращает markdown с заголовком и сообщениями."""
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(
            title="Тест экспорта",
            preset_id=preset.id,
        )
        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content_text="Привет",
        )
        await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content_text="Ответ",
        )
        await session.commit()
        conv_id = conv.id

    response = await client.get(f"/api/conversations/{conv_id}/export")
    assert response.status_code == 200
    assert "text/markdown" in response.headers.get("content-type", "")
    body = response.text
    assert "# Тест экспорта" in body
    assert "Привет" in body
    assert "Ответ" in body
    assert "Пользователь" in body
    assert "Ассистент" in body


@pytest.mark.asyncio
async def test_export_not_found(client: AsyncClient) -> None:
    """404 для несуществующей беседы."""
    response = await client.get(f"/api/conversations/{uuid.uuid4()}/export")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_search_messages(client: AsyncClient) -> None:
    """Поиск находит подстроку в content_text."""
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(
            title="Поисковая беседа",
            preset_id=preset.id,
        )
        await MessageRepository(session).create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content_text="Уникальная фраза для поиска xyz",
        )
        await session.commit()
        conv_id = conv.id

    listed = await client.get(f"/api/conversations/{conv_id}/messages")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    response = await client.get("/api/search?q=xyz")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["conversation_title"] == "Поисковая беседа"
    assert "xyz" in data[0]["snippet"].lower()
    assert data[0]["match_kind"] == "message"


@pytest.mark.asyncio
async def test_search_by_conversation_title(client: AsyncClient) -> None:
    """Поиск находит беседу по слову в названии."""
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        await ConversationRepository(session).create(
            title="Queen Chrysalis токены",
            preset_id=preset.id,
        )
        await session.commit()

    response = await client.get("/api/search?q=Chrysalis")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(h["match_kind"] == "title" for h in data)
    assert any("Chrysalis" in h["snippet"] for h in data)


@pytest.mark.asyncio
async def test_search_any_word_in_message(client: AsyncClient) -> None:
    """Достаточно совпадения одного слова из запроса."""
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        conv = await ConversationRepository(session).create(
            title="Тест",
            preset_id=preset.id,
        )
        await MessageRepository(session).create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content_text="В сообщении есть только слово альфа",
        )
        await session.commit()

    response = await client.get("/api/search?q=альфа бета")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert "альфа" in data[0]["snippet"].lower()
