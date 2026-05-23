"""Тесты автогенерации заголовка беседы."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.constants import DEFAULT_CONVERSATION_TITLE
from app.db import session as db_session
from app.db.models import Message, MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, PresetRepository
from app.services.conversation_title_service import (
    _excerpts_from_messages,
    _normalize_title,
    maybe_generate_conversation_title,
)
from tests.cleanup import record_test_conversation_id


def test_normalize_title_strips_quotes() -> None:
    """Заголовок очищается от кавычек и лишних пробелов."""
    assert _normalize_title('  "Кот в космосе"  ') == "Кот в космосе"


def test_normalize_title_from_reasoning_with_quote() -> None:
    """Из рассуждения с кавычками извлекается короткое название."""
    raw = (
        'Выберу наиболее подходящий вариант: "Создание изображения Твайлайт Спаркл '
        'и Рэйнбоу Дэш" - это 6 слов'
    )
    title = _normalize_title(raw)
    assert title == "Создание изображения Твайлайт Спаркл и Рэйнбоу Дэш"
    assert len(title.split()) <= 7


def test_normalize_title_truncates_long_plain() -> None:
    """Слишком длинный ответ обрезается до 7 слов."""
    raw = "Очень длинное название беседы про генерацию картинок и персонажей мультфильма"
    title = _normalize_title(raw)
    assert len(title.split()) == 7


def test_excerpts_limits_to_three() -> None:
    """Берутся не более трёх фрагментов."""
    conv_id = uuid.uuid4()
    messages = [
        Message(
            id=uuid.uuid4(),
            conversation_id=conv_id,
            role=MessageRole.USER,
            content_text=f"msg{i}",
        )
        for i in range(5)
    ]
    excerpts = _excerpts_from_messages(messages, max_posts=3)
    assert len(excerpts) == 3


@pytest.mark.asyncio
async def test_maybe_generate_title_updates_conversation(client: AsyncClient) -> None:
    """При дефолтном заголовке LLM переименовывает беседу."""
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None

        conv_repo = ConversationRepository(session)
        conv = await conv_repo.create(
            title=DEFAULT_CONVERSATION_TITLE,
            preset_id=preset.id,
        )
        record_test_conversation_id(conv.id)

        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content_text="Расскажи про квантовые компьютеры",
        )
        await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content_text="Квантовые компьютеры используют кубиты…",
        )
        await session.commit()

        llm = AsyncMock()
        llm.complete_plain_text = AsyncMock(return_value="Квантовые компьютеры")

        new_title = await maybe_generate_conversation_title(session, conv.id, llm)
        await session.commit()

    assert new_title == "Квантовые компьютеры"

    async with db_session.async_session_factory() as session:
        updated = await ConversationRepository(session).get_by_id(conv.id)
    assert updated is not None
    assert updated.title == "Квантовые компьютеры"


@pytest.mark.asyncio
async def test_maybe_generate_skips_after_second_user_message(client: AsyncClient) -> None:
    """После второго сообщения пользователя автозаголовок не вызывается."""
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None

        conv_repo = ConversationRepository(session)
        conv = await conv_repo.create(
            title=DEFAULT_CONVERSATION_TITLE,
            preset_id=preset.id,
        )
        record_test_conversation_id(conv.id)
        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content_text="Первый вопрос",
        )
        await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content_text="Первый ответ",
        )
        await msg_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content_text="Второй вопрос",
        )
        await session.commit()

        llm = AsyncMock()
        result = await maybe_generate_conversation_title(session, conv.id, llm)

    assert result is None
    llm.complete_plain_text.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_generate_skips_custom_title(
    client: AsyncClient,
    repo_conv_title: str,
) -> None:
    """Пользовательский заголовок не перезаписывается."""
    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None

        conv_repo = ConversationRepository(session)
        conv = await conv_repo.create(title=repo_conv_title, preset_id=preset.id)
        await session.commit()

        llm = AsyncMock()
        result = await maybe_generate_conversation_title(session, conv.id, llm)

    assert result is None
    llm.complete_plain_text.assert_not_called()
