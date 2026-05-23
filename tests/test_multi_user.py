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
