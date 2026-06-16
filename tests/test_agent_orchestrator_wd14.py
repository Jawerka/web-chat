"""WD14 в orchestrator: только user-вложения текущего хода."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db import session as db_session
from app.db.models import MessageRole
from app.db.repositories import MessageRepository
from app.integrations.llm_client import LLMCompletion, LLMError
from app.services.agent_orchestrator import AgentOrchestrator
from app.services.message_builder import Wd14TagEntry
from tests.helpers import api_create_conversation, minimal_valid_png_bytes


@pytest.mark.asyncio
async def test_wd14_tags_in_llm_not_in_stored_content(
    client,
    test_conv_title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "wd_tagger_enabled", True)

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    files = [("files", ("wd14-test.png", minimal_valid_png_bytes(), "image/png"))]
    up = await client.post(
        "/api/upload",
        files=files,
        data={"conversation_id": conv["id"]},
    )
    assert up.status_code == 200, up.text
    att_id = up.json()["attachments"][0]["id"]

    mock_entries = [
        Wd14TagEntry(attachment_id=att_id, filename="wd14-test.png", tags="1girl, solo"),
    ]

    with patch(
        "app.services.agent_orchestrator.tag_user_attachments",
        new_callable=AsyncMock,
        return_value=mock_entries,
    ) as tag_mock:
        mock_llm = MagicMock()
        mock_llm.parse_tool_arguments = MagicMock(return_value={})
        mock_llm.complete_with_stream = AsyncMock(
            return_value=LLMCompletion(content="ok", tool_calls=None, finish_reason="stop"),
        )
        orchestrator = AgentOrchestrator(llm=mock_llm)
        emit = AsyncMock()

        await orchestrator.run_conversation_turn(
            conv_id,
            "опиши",
            [uuid.UUID(att_id)],
            emit,
            asyncio.Event(),
            wd_tagger=True,
        )

        tag_mock.assert_awaited_once()

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        messages = await msg_repo.list_for_conversation(conv_id)
        user = next(m for m in messages if m.role == MessageRole.USER)
        assert user.content_text == "опиши"
        assert "[WD14" not in (user.content_text or "")
        assert user.content_json is not None
        assert user.content_json.get("wd14")
        assert user.content_json["wd14"][0]["tags"] == "1girl, solo"


@pytest.mark.asyncio
async def test_wd14_skipped_when_client_disabled(
    client,
    test_conv_title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "wd_tagger_enabled", True)

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    files = [("files", ("x.png", minimal_valid_png_bytes(), "image/png"))]
    up = await client.post("/api/upload", files=files)
    att_id = up.json()["attachments"][0]["id"]

    with patch(
        "app.services.agent_orchestrator.tag_user_attachments",
        new_callable=AsyncMock,
    ) as tag_mock:
        mock_llm = MagicMock()
        mock_llm.parse_tool_arguments = MagicMock(return_value={})
        mock_llm.complete_with_stream = AsyncMock(
            return_value=LLMCompletion(content="ok", tool_calls=None, finish_reason="stop"),
        )
        orchestrator = AgentOrchestrator(llm=mock_llm)
        await orchestrator.run_conversation_turn(
            conv_id,
            "hi",
            [uuid.UUID(att_id)],
            AsyncMock(),
            asyncio.Event(),
            wd_tagger=False,
        )
        tag_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_wd14_called_once_per_turn_not_for_history(
    client,
    test_conv_title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Один user attach → один вызов tag_user_attachments (не по истории)."""
    from app.config import settings

    monkeypatch.setattr(settings, "wd_tagger_enabled", True)

    conv = await api_create_conversation(client, test_conv_title)
    conv_id = uuid.UUID(conv["id"])

    files = [("files", ("one.png", minimal_valid_png_bytes(), "image/png"))]
    up = await client.post("/api/upload", files=files)
    att_id = up.json()["attachments"][0]["id"]

    with patch(
        "app.services.agent_orchestrator.tag_user_attachments",
        new_callable=AsyncMock,
        return_value=[],
    ) as tag_mock:
        mock_llm = MagicMock()
        mock_llm.parse_tool_arguments = MagicMock(return_value={})
        mock_llm.complete_with_stream = AsyncMock(
            side_effect=LLMError("stop after tag check"),
        )
        orchestrator = AgentOrchestrator(llm=mock_llm)
        with pytest.raises(LLMError):
            await orchestrator.run_conversation_turn(
                conv_id,
                "test",
                [uuid.UUID(att_id)],
                AsyncMock(),
                asyncio.Event(),
                wd_tagger=True,
            )
        assert tag_mock.await_count == 1
        args, _kwargs = tag_mock.call_args
        attachments = args[1]
        assert len(attachments) == 1
