"""Тесты удаления и редактирования сообщений."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select

from app.db import session as db_session
from app.db.models import Message, MessageRole
from app.db.repositories import MessageRepository


async def _seed_conversation(client: AsyncClient) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    conv = await client.post("/api/conversations", json={})
    cid = uuid.UUID(conv.json()["id"])

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        user = await msg_repo.create(
            conversation_id=cid,
            role=MessageRole.USER,
            content_text="Привет",
        )
        assistant = await msg_repo.create(
            conversation_id=cid,
            role=MessageRole.ASSISTANT,
            content_text="Ответ",
        )
        await session.commit()
        return cid, user.id, assistant.id


async def test_messages_visible_after_seed(client: AsyncClient) -> None:
    cid, _, _ = await _seed_conversation(client)
    resp = await client.get(f"/api/conversations/{cid}/messages")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_delete_user_cascades_following(client: AsyncClient) -> None:
    cid, user_id, _assistant_id = await _seed_conversation(client)
    r = await client.delete(f"/api/conversations/{cid}/messages/{user_id}?cascade=true")
    assert r.status_code == 204

    async with db_session.async_session_factory() as session:
        q = select(Message).where(Message.conversation_id == cid)
        rows = (await session.execute(q)).scalars().all()
    assert rows == []


async def test_delete_assistant_only(client: AsyncClient) -> None:
    cid, user_id, assistant_id = await _seed_conversation(client)
    r = await client.delete(f"/api/conversations/{cid}/messages/{assistant_id}?cascade=false")
    assert r.status_code == 204

    async with db_session.async_session_factory() as session:
        q = select(Message).where(Message.conversation_id == cid)
        rows = (await session.execute(q)).scalars().all()
    assert len(rows) == 1
    assert rows[0].role == MessageRole.USER
    assert rows[0].id == user_id


async def test_patch_assistant_without_cascade(client: AsyncClient) -> None:
    cid, _user_id, assistant_id = await _seed_conversation(client)
    r = await client.patch(
        f"/api/conversations/{cid}/messages/{assistant_id}",
        json={"content_text": "Новый ответ"},
    )
    assert r.status_code == 200
    assert r.json()["content_text"] == "Новый ответ"
