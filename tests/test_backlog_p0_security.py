"""P0-S1..S3: изоляция вложений и политика trusted internal (BACKLOG)."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.db import session as db_session
from app.db.models import MessageRole
from app.db.repositories import AttachmentRepository, MessageRepository, PresetRepository
from app.integrations.tool_executor import ToolExecutor
from app.security import trusted_internal as ti
from app.security.trusted_internal import (
    integration_host_may_register,
    invalidate_trusted_internal_cache,
    register_integration_urls,
)
from tests.helpers import repo_create_conversation
from tests.safety import assert_not_using_production_database


@pytest.mark.asyncio
async def test_link_to_message_rejects_other_conversation(
    tmp_path,
    repo_conv_title: str,
) -> None:
    from app.db.session import dispose_database, init_db
    from tests.safety import safe_configure_database

    await dispose_database()
    safe_configure_database(f"sqlite+aiosqlite:///{tmp_path / 'link.sqlite'}")
    await init_db()
    assert_not_using_production_database()

    async with db_session.async_session_factory() as session:
        preset_repo = PresetRepository(session)
        preset = await preset_repo.get_default()
        assert preset is not None
        conv_a = await repo_create_conversation(session, preset.id, repo_conv_title)
        conv_b = await repo_create_conversation(
            session,
            preset.id,
            f"{repo_conv_title}-b",
        )
        att_repo = AttachmentRepository(session)
        att = await att_repo.create(
            attachment_id=uuid.uuid4(),
            original_name="doc.pdf",
            mime_type="application/pdf",
            size_bytes=4,
            storage_path="x/doc.pdf",
            conversation_id=conv_b.id,
        )
        msg_repo = MessageRepository(session)
        user = await msg_repo.create(
            conversation_id=conv_a.id,
            role=MessageRole.USER,
            content_text="hi",
            content_json=None,
        )
        with pytest.raises(ValueError, match="другой беседе"):
            await att_repo.link_to_message(
                [att.id],
                message_id=user.id,
                conversation_id=conv_a.id,
            )


@pytest.mark.asyncio
async def test_extract_text_rejects_cross_conversation_attachment(
    tmp_path,
    repo_conv_title: str,
) -> None:
    from app.db.session import dispose_database, init_db
    from tests.safety import safe_configure_database

    await dispose_database()
    safe_configure_database(f"sqlite+aiosqlite:///{tmp_path / 'tool.sqlite'}")
    await init_db()
    assert_not_using_production_database()

    async with db_session.async_session_factory() as session:
        preset_repo = PresetRepository(session)
        preset = await preset_repo.get_default()
        assert preset is not None
        conv_a = await repo_create_conversation(session, preset.id, repo_conv_title)
        conv_b = await repo_create_conversation(
            session,
            preset.id,
            f"{repo_conv_title}-b",
        )
        att_repo = AttachmentRepository(session)
        att = await att_repo.create(
            attachment_id=uuid.uuid4(),
            original_name="note.txt",
            mime_type="text/plain",
            size_bytes=3,
            storage_path="x/note.txt",
            conversation_id=conv_b.id,
        )
        await att_repo.update_extracted_text(att, "secret")
        await session.commit()

        executor = ToolExecutor(session, conversation_id=conv_a.id)
        result = await executor.run(
            "extract_text",
            {"attachment_id": str(att.id)},
        )
        assert "другой беседе" in result.content


def test_integration_host_may_register_blocks_public_when_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "llm_base_url", "http://192.168.88.41:8989/v1")
    monkeypatch.setattr(settings, "sd_webui_url", "http://192.168.88.52:7860")
    monkeypatch.setattr(settings, "public_base_url", "http://192.168.88.44:8090")
    monkeypatch.setattr(settings, "trusted_internal_ips", "")
    assert integration_host_may_register("192.168.88.41") is True
    assert integration_host_may_register("evil.example.com") is False
    assert integration_host_may_register("8.8.8.8") is False


def test_register_integration_urls_skips_untrusted_host_when_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "llm_base_url", "http://192.168.88.41:8989/v1")
    monkeypatch.setattr(settings, "sd_webui_url", "http://192.168.88.52:7860")
    monkeypatch.setattr(settings, "public_base_url", "http://192.168.88.44:8090")
    monkeypatch.setattr(ti, "_dynamic_hosts", set())
    invalidate_trusted_internal_cache()
    register_integration_urls("http://evil.example.com:8989/v1", None)
    assert "evil.example.com" not in ti._dynamic_hosts
    register_integration_urls("http://192.168.88.99:8989/v1", None)
    assert "192.168.88.99" in ti._dynamic_hosts


def test_register_integration_urls_allows_any_host_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(ti, "_dynamic_hosts", set())
    invalidate_trusted_internal_cache()
    register_integration_urls("http://evil.example.com:8989/v1", None)
    assert "evil.example.com" in ti._dynamic_hosts
