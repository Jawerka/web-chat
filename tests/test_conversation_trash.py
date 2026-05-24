"""Тесты корзины бесед (мягкое удаление, восстановление, окончательное удаление)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.db.repositories import ConversationRepository
from app.services.trash_service import purge_expired_trash
from tests.helpers import api_create_conversation


@pytest.mark.asyncio
async def test_soft_delete_moves_to_trash(client: AsyncClient, test_conv_title: str) -> None:
    """DELETE убирает беседу из списка и помещает в корзину."""
    created = await api_create_conversation(client, test_conv_title)
    conv_id = created["id"]

    deleted = await client.delete(f"/api/conversations/{conv_id}")
    assert deleted.status_code == 204

    listing = await client.get("/api/conversations")
    assert listing.status_code == 200
    assert not any(c["id"] == conv_id for c in listing.json())

    missing = await client.get(f"/api/conversations/{conv_id}")
    assert missing.status_code == 404

    trash = await client.get("/api/conversations/trash")
    assert trash.status_code == 200
    in_trash = [c for c in trash.json() if c["id"] == conv_id]
    assert len(in_trash) == 1
    assert in_trash[0]["title"] == test_conv_title
    assert in_trash[0]["deleted_at"] is not None


@pytest.mark.asyncio
async def test_restore_from_trash(client: AsyncClient, test_conv_title: str) -> None:
    """POST restore возвращает беседу в основной список."""
    created = await api_create_conversation(client, test_conv_title)
    conv_id = created["id"]

    await client.delete(f"/api/conversations/{conv_id}")

    restored = await client.post(f"/api/conversations/{conv_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None

    listing = await client.get("/api/conversations")
    assert any(c["id"] == conv_id for c in listing.json())

    trash = await client.get("/api/conversations/trash")
    assert not any(c["id"] == conv_id for c in trash.json())


@pytest.mark.asyncio
async def test_permanent_delete_from_trash(client: AsyncClient, test_conv_title: str) -> None:
    """DELETE permanent удаляет беседу из корзины навсегда."""
    created = await api_create_conversation(client, test_conv_title)
    conv_id = created["id"]

    await client.delete(f"/api/conversations/{conv_id}")

    permanent = await client.delete(f"/api/conversations/{conv_id}/permanent")
    assert permanent.status_code == 204

    trash = await client.get("/api/conversations/trash")
    assert not any(c["id"] == conv_id for c in trash.json())

    restore = await client.post(f"/api/conversations/{conv_id}/restore")
    assert restore.status_code == 404


@pytest.mark.asyncio
async def test_trashed_conversation_excluded_from_search(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """Поиск не возвращает беседы и сообщения из корзины."""
    token = "pytesttrashsearch"
    title = f"{test_conv_title} {token}"
    created = await api_create_conversation(client, title)
    conv_id = created["id"]

    await client.post(
        f"/api/conversations/{conv_id}/turn",
        json={"content": f"уникальный текст {token} для поиска"},
    )

    before = await client.get(f"/api/search?q={token}")
    assert before.status_code == 200
    assert any(h["conversation_id"] == conv_id for h in before.json())

    await client.delete(f"/api/conversations/{conv_id}")

    after = await client.get(f"/api/search?q={token}")
    assert after.status_code == 200
    assert not any(h["conversation_id"] == conv_id for h in after.json())


@pytest.mark.asyncio
async def test_purge_expired_trash(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """Фоновая очистка удаляет беседы в корзине старше trash_retention_days."""
    created = await api_create_conversation(client, test_conv_title)
    conv_id = created["id"]

    await client.delete(f"/api/conversations/{conv_id}")

    from app.db import session as db_session

    async with db_session.async_session_factory() as session:
        repo = ConversationRepository(session)
        conv = await repo.get_by_id(uuid.UUID(conv_id))
        assert conv is not None
        conv.deleted_at = datetime.now(UTC) - timedelta(days=30)
        await session.commit()

    async with db_session.async_session_factory() as session:
        removed = await purge_expired_trash(session)
        await session.commit()
    assert removed >= 1

    trash = await client.get("/api/conversations/trash")
    assert not any(c["id"] == conv_id for c in trash.json())


@pytest.mark.asyncio
async def test_empty_trash(client: AsyncClient, test_conv_title: str) -> None:
    """DELETE /trash окончательно удаляет все беседы в корзине пользователя."""
    a = await api_create_conversation(client, f"{test_conv_title} A")
    b = await api_create_conversation(client, f"{test_conv_title} B")
    await client.delete(f"/api/conversations/{a['id']}")
    await client.delete(f"/api/conversations/{b['id']}")

    trash_before = await client.get("/api/conversations/trash")
    assert len(trash_before.json()) >= 2

    emptied = await client.delete("/api/conversations/trash")
    assert emptied.status_code == 200
    assert emptied.json()["deleted"] >= 2

    trash_after = await client.get("/api/conversations/trash")
    assert trash_after.json() == []
