"""P2.2: изоляция бесед по пользователю."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.mark.asyncio
async def test_multi_user_isolates_conversations(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "multi_user_enabled", True)

    r_alice = await client.post(
        "/api/conversations",
        json={"title": "alice-chat"},
        headers={"X-Web-Chat-User": "alice"},
    )
    assert r_alice.status_code == 201
    alice_id = r_alice.json()["id"]

    r_bob = await client.post(
        "/api/conversations",
        json={"title": "bob-chat"},
        headers={"X-Web-Chat-User": "bob"},
    )
    assert r_bob.status_code == 201

    list_alice = await client.get(
        "/api/conversations",
        headers={"X-Web-Chat-User": "alice"},
    )
    assert list_alice.status_code == 200
    alice_ids = {c["id"] for c in list_alice.json()}
    assert alice_id in alice_ids
    assert r_bob.json()["id"] not in alice_ids

    r_forbidden = await client.get(
        f"/api/conversations/{r_bob.json()['id']}",
        headers={"X-Web-Chat-User": "alice"},
    )
    assert r_forbidden.status_code == 404


@pytest.mark.asyncio
async def test_multi_user_disabled_ignores_header(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "multi_user_enabled", False)

    r1 = await client.post(
        "/api/conversations",
        json={"title": "shared"},
        headers={"X-Web-Chat-User": "alice"},
    )
    r2 = await client.post(
        "/api/conversations",
        json={"title": "shared-2"},
        headers={"X-Web-Chat-User": "bob"},
    )
    assert r1.status_code == 201 and r2.status_code == 201

    listed = await client.get("/api/conversations")
    ids = {c["id"] for c in listed.json()}
    assert r1.json()["id"] in ids
    assert r2.json()["id"] in ids


@pytest.mark.asyncio
async def test_assign_orphan_conversations(
    client: AsyncClient,
) -> None:
    from app.db import session as db_session
    from app.db.repositories import ConversationRepository, PresetRepository, UserRepository

    from app.security.passwords import hash_password

    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        await ConversationRepository(session).create(
            title="[pytest] orphan owner",
            preset_id=preset.id,
            owner_user_id=None,
        )
        orphans_before = await ConversationRepository(session).count_orphans()
        assert orphans_before >= 1
        user = await UserRepository(session).create_user(
            login="legacy",
            slug="legacy",
            display_name="legacy",
            password_hash=hash_password("test"),
        )
        assigned = await ConversationRepository(session).assign_orphan_conversations(user.id)
        await session.commit()
        assert assigned >= 1


@pytest.mark.asyncio
async def test_conversation_quota(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "multi_user_enabled", True)
    monkeypatch.setattr(settings, "multi_user_max_conversations", 1)

    headers = {"X-Web-Chat-User": "quota-user"}
    r1 = await client.post(
        "/api/conversations",
        json={"title": "first"},
        headers=headers,
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/conversations",
        json={"title": "second"},
        headers=headers,
    )
    assert r2.status_code == 403
    body = r2.json()
    assert body["detail"]["code"] == "quota_exceeded"
