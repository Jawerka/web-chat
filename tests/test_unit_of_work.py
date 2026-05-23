"""Тесты Unit of Work (P1.1)."""

from __future__ import annotations

import uuid

import pytest

from app.db import session as db_session
from app.db.models import MessageRole
from app.db.uow import SqlAlchemyUnitOfWork


@pytest.mark.asyncio
async def test_uow_creates_message_and_commits(test_conv_title: str, client) -> None:
    from tests.helpers import api_create_conversation

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    async with db_session.async_session_factory() as session:
        async with SqlAlchemyUnitOfWork(session) as uow:
            msg = await uow.messages.create(
                conversation_id=conv_id,
                role=MessageRole.USER,
                content_text="uow test",
            )
            assert msg.id is not None

    async with db_session.async_session_factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        listed = await uow.messages.list_for_conversation(conv_id, limit=10)
        assert any(m.content_text == "uow test" for m in listed)


@pytest.mark.asyncio
async def test_uow_rollback_on_exception(test_conv_title: str, client) -> None:
    from tests.helpers import api_create_conversation

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    async with db_session.async_session_factory() as session:
        with pytest.raises(RuntimeError):
            async with SqlAlchemyUnitOfWork(session) as uow:
                await uow.messages.create(
                    conversation_id=conv_id,
                    role=MessageRole.USER,
                    content_text="rollback me",
                )
                raise RuntimeError("abort")

    async with db_session.async_session_factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        listed = await uow.messages.list_for_conversation(conv_id, limit=10)
        assert not any(m.content_text == "rollback me" for m in listed)
