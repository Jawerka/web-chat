"""Контекст беседы для LLM: история и изображения."""

from __future__ import annotations

import uuid

import pytest

from app.db import session as db_session
from app.db.models import MessageRole
from app.db.repositories import MessageRepository
from app.services.llm_context import build_conversation_llm_context
from app.services.message_builder import message_to_llm_dict
from tests.helpers import api_create_conversation


def test_assistant_images_in_llm_dict_multimodal() -> None:
    from app.db.models import Message

    msg = Message(
        conversation_id=uuid.uuid4(),
        role=MessageRole.ASSISTANT,
        content_text="Вот картинка",
        content_json={
            "images": ["/media/asset/11111111-1111-1111-1111-111111111111.png"],
            "image_asset_ids": ["11111111-1111-1111-1111-111111111111"],
        },
    )
    entry = message_to_llm_dict(msg)
    assert entry["role"] == "assistant"
    assert isinstance(entry["content"], list)
    types = [p["type"] for p in entry["content"]]
    assert "text" in types
    assert "image_url" in types
    img_part = next(p for p in entry["content"] if p["type"] == "image_url")
    assert "/llm" in img_part["image_url"]["url"]


def test_assistant_with_tool_calls_gets_image_note() -> None:
    from app.db.models import Message

    msg = Message(
        conversation_id=uuid.uuid4(),
        role=MessageRole.ASSISTANT,
        content_text="calling tool",
        content_json={
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
            "images": ["/media/asset/22222222-2222-2222-2222-222222222222"],
        },
    )
    entry = message_to_llm_dict(msg)
    assert entry.get("tool_calls")
    assert "изображения" in (entry.get("content") or "").lower()


@pytest.mark.asyncio
async def test_build_llm_context_from_db(client, test_conv_title: str) -> None:
    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        await msg_repo.create(
            conversation_id=conv_id,
            role=MessageRole.USER,
            content_text="Привет",
            content_json={
                "parts": [
                    {"type": "text", "text": "Привет"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "/media/asset/33333333-3333-3333-3333-333333333333"},
                    },
                ],
            },
        )
        await msg_repo.create(
            conversation_id=conv_id,
            role=MessageRole.ASSISTANT,
            content_text="Ответ",
            content_json={
                "images": ["/media/asset/44444444-4444-4444-4444-444444444444"],
            },
        )
        await session.commit()

        ctx = await build_conversation_llm_context(session, conv_id)
        assert ctx["messages_in_context"] >= 2
        roles = [m["role"] for m in ctx["messages"]]
        assert "user" in roles
        assert "assistant" in roles
        user = next(m for m in ctx["messages"] if m["role"] == "user")
        assert isinstance(user["content"], list)


@pytest.mark.asyncio
async def test_api_llm_context_endpoint(client, test_conv_title: str) -> None:
    conv = await api_create_conversation(client, test_conv_title)
    conv_id = conv["id"]
    resp = await client.get(f"/api/conversations/{conv_id}/llm-context")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"] == conv_id
    assert "messages" in data
