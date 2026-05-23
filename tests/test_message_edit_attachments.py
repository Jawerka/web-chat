"""Редактирование user-сообщения с вложениями."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.db import session as db_session
from app.db.models import MessageRole
from app.db.repositories import AttachmentRepository, MessageRepository
from tests.helpers import api_create_conversation


@pytest.mark.asyncio
async def test_list_and_patch_message_attachments(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    conv = await api_create_conversation(client, test_conv_title)
    cid = uuid.UUID(conv["id"])

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
        b"\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    up1 = await client.post(
        "/api/upload",
        files=[("files", ("a.png", png, "image/png"))],
        data={"conversation_id": str(cid)},
    )
    up2 = await client.post(
        "/api/upload",
        files=[("files", ("b.png", png, "image/png"))],
        data={"conversation_id": str(cid)},
    )
    att1 = uuid.UUID(up1.json()["attachments"][0]["id"])
    att2 = uuid.UUID(up2.json()["attachments"][0]["id"])

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        att_repo = AttachmentRepository(session)
        user = await msg_repo.create(
            conversation_id=cid,
            role=MessageRole.USER,
            content_text="hello",
            content_json={"parts": [{"type": "text", "text": "hello"}]},
        )
        await att_repo.link_to_message([att1], message_id=user.id, conversation_id=cid)
        await session.commit()
        mid = user.id

    listed = await client.get(f"/api/conversations/{cid}/messages/{mid}/attachments")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == str(att1)

    patched = await client.patch(
        f"/api/conversations/{cid}/messages/{mid}",
        json={"content_text": "updated", "attachment_ids": [str(att2)]},
    )
    assert patched.status_code == 200

    listed2 = await client.get(f"/api/conversations/{cid}/messages/{mid}/attachments")
    assert len(listed2.json()) == 1
    assert listed2.json()[0]["id"] == str(att2)

    async with db_session.async_session_factory() as session:
        repo = AttachmentRepository(session)
        old = await repo.get_by_id(att1)
        assert old is not None
        assert old.message_id is None
