"""Тесты серверного черновика composer (handoff внешней галереи)."""

from __future__ import annotations

import asyncio
import base64
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db import session as db_session
from app.db.models import Attachment, Conversation
from app.integrations.llm_client import LLMError
from app.services.agent_orchestrator import AgentOrchestrator
from tests.helpers import format_test_conversation_title

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_from_image_persists_composer_draft_text(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    title = format_test_conversation_title("draft_persist")
    r = await client.post(
        "/api/conversations/from-image",
        data={"text": "1girl, solo, test", "title": title, "preset_slug": "img2img"},
        files=[("image", ("shot.png", MINIMAL_PNG, "image/png"))],
    )
    assert r.status_code == 201, r.text
    conv_id = uuid.UUID(r.json()["conversation_id"])

    async with db_session.async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None
        assert conv.composer_draft_text == "1girl, solo, test"


@pytest.mark.asyncio
async def test_get_conversation_returns_server_draft(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    title = format_test_conversation_title("draft_get")
    r = await client.post(
        "/api/conversations/from-image",
        data={"text": "tags here", "title": title, "preset_slug": "img2img"},
        files=[("image", ("a.png", MINIMAL_PNG, "image/png"))],
    )
    assert r.status_code == 201
    data = r.json()
    conv_id = data["conversation_id"]

    detail = await client.get(f"/api/conversations/{conv_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["message_count"] == 0
    assert body["composer_text"] == "tags here"
    assert len(body["pending_attachments"]) == 1
    assert body["pending_attachments"][0]["id"] == data["attachments"][0]["id"]
    assert body["pending_attachments"][0]["preview_url"]


@pytest.mark.asyncio
async def test_post_conversation_text_only_handoff(client: AsyncClient) -> None:
    title = format_test_conversation_title("text_only")
    r = await client.post(
        "/api/conversations",
        json={
            "title": title,
            "text": "video tags only",
            "preset_slug": "default",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["composer_text"] == "video tags only"
    assert data["chat_url"] == f"/?conv={data['conversation_id']}"
    assert data["conversation_id"] == data["id"]
    assert data["attachments"] == []

    detail = await client.get(f"/api/conversations/{data['id']}")
    assert detail.status_code == 200
    assert detail.json()["composer_text"] == "video tags only"
    assert detail.json()["pending_attachments"] == []


@pytest.mark.asyncio
async def test_composer_draft_cleared_after_turn(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    title = format_test_conversation_title("draft_clear")
    r = await client.post(
        "/api/conversations/from-image",
        data={"text": "will send", "title": title, "preset_slug": "img2img"},
        files=[("image", ("b.png", MINIMAL_PNG, "image/png"))],
    )
    assert r.status_code == 201
    conv_id = uuid.UUID(r.json()["conversation_id"])
    att_id = r.json()["attachments"][0]["id"]

    mock_llm = MagicMock()
    mock_llm.parse_tool_arguments = MagicMock(return_value={})
    mock_llm.complete_with_stream = AsyncMock(side_effect=LLMError("skip llm"))

    orchestrator = AgentOrchestrator(llm=mock_llm)

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    with pytest.raises(LLMError):
        await orchestrator.run_conversation_turn(
            conv_id,
            "user sends",
            [uuid.UUID(att_id)],
            emit,
            asyncio.Event(),
        )

    async with db_session.async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None
        assert conv.composer_draft_text is None

        att_rows = await session.execute(
            select(Attachment).where(Attachment.id == uuid.UUID(att_id)),
        )
        att = att_rows.scalar_one()
        assert att.message_id is not None


@pytest.mark.asyncio
async def test_purge_orphan_pending_on_send_without_attachment(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """При отправке без attachment_ids сиротные pending удаляются из БД."""
    title = format_test_conversation_title("purge_orphan")
    r = await client.post(
        "/api/conversations/from-image",
        data={"text": "orphan test", "title": title, "preset_slug": "img2img"},
        files=[("image", ("c.png", MINIMAL_PNG, "image/png"))],
    )
    assert r.status_code == 201
    conv_id = uuid.UUID(r.json()["conversation_id"])
    att_id = uuid.UUID(r.json()["attachments"][0]["id"])

    mock_llm = MagicMock()
    mock_llm.parse_tool_arguments = MagicMock(return_value={})
    mock_llm.complete_with_stream = AsyncMock(side_effect=LLMError("skip"))

    orchestrator = AgentOrchestrator(llm=mock_llm)

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    with pytest.raises(LLMError):
        await orchestrator.run_conversation_turn(
            conv_id,
            "text only no attach",
            [],
            emit,
            asyncio.Event(),
        )

    async with db_session.async_session_factory() as session:
        orphan = await session.get(Attachment, att_id)
        assert orphan is None

    detail = await client.get(f"/api/conversations/{conv_id}")
    body = detail.json()
    assert body["message_count"] >= 1
    assert body["pending_attachments"] == []


@pytest.mark.asyncio
async def test_get_hides_pending_when_conversation_has_messages(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """GET не отдаёт pending, если в беседе уже есть сообщения (даже при сироте в БД)."""
    from app.db.models import Message, MessageRole
    from app.db.repositories import AttachmentRepository, MessageRepository
    from tests.helpers import api_create_conversation

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=conv_id,
            role=MessageRole.USER,
            content_text="already sent",
        )
        att_repo = AttachmentRepository(session)
        await att_repo.create(
            attachment_id=uuid.uuid4(),
            original_name="ghost.png",
            mime_type="image/png",
            size_bytes=len(MINIMAL_PNG),
            storage_path="",
            conversation_id=conv_id,
        )
        await session.commit()

    detail = await client.get(f"/api/conversations/{conv_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["message_count"] >= 1
    assert body["composer_text"] == ""
    assert body["pending_attachments"] == []


@pytest.mark.asyncio
async def test_get_empty_pending_after_send_with_attachment(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    title = format_test_conversation_title("pending_after_send")
    r = await client.post(
        "/api/conversations/from-image",
        data={"text": "send with img", "title": title, "preset_slug": "img2img"},
        files=[("image", ("d.png", MINIMAL_PNG, "image/png"))],
    )
    assert r.status_code == 201
    conv_id = r.json()["conversation_id"]
    att_id = r.json()["attachments"][0]["id"]

    mock_llm = MagicMock()
    mock_llm.parse_tool_arguments = MagicMock(return_value={})
    mock_llm.complete_with_stream = AsyncMock(side_effect=LLMError("skip"))

    orchestrator = AgentOrchestrator(llm=mock_llm)

    async def emit(_event_type: str, _payload: dict) -> None:
        return None

    with pytest.raises(LLMError):
        await orchestrator.run_conversation_turn(
            uuid.UUID(conv_id),
            "with image",
            [uuid.UUID(att_id)],
            emit,
            asyncio.Event(),
        )

    detail = await client.get(f"/api/conversations/{conv_id}")
    body = detail.json()
    assert body["message_count"] >= 1
    assert body["pending_attachments"] == []
    assert body["composer_text"] == ""

    async with db_session.async_session_factory() as session:
        pending = await session.execute(
            select(Attachment).where(
                Attachment.conversation_id == uuid.UUID(conv_id),
                Attachment.message_id.is_(None),
            ),
        )
        assert list(pending.scalars().all()) == []
