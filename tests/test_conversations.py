"""Тесты REST API бесед и пресетов (этап 2)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_presets_seeded(client: AsyncClient) -> None:
    """После старта доступны три seed-пресета."""
    response = await client.get("/api/presets")
    assert response.status_code == 200
    presets = response.json()
    assert len(presets) == 3
    slugs = {p["slug"] for p in presets}
    assert slugs == {"default", "image_gen", "document_analysis"}
    defaults = [p for p in presets if p["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["slug"] == "default"


@pytest.mark.asyncio
async def test_create_conversation_without_preset_id(client: AsyncClient) -> None:
    """POST /api/conversations без preset_id использует default."""
    response = await client.post("/api/conversations", json={})
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Новая беседа"
    assert "id" in data
    assert "preset_id" in data

    presets = (await client.get("/api/presets")).json()
    default_preset = next(p for p in presets if p["is_default"])
    assert data["preset_id"] == default_preset["id"]


@pytest.mark.asyncio
async def test_list_and_get_conversation(client: AsyncClient) -> None:
    """Список бесед и GET по id."""
    created = await client.post(
        "/api/conversations",
        json={"title": "Тестовая беседа"},
    )
    conv_id = created.json()["id"]

    listing = await client.get("/api/conversations")
    assert listing.status_code == 200
    assert any(c["id"] == conv_id for c in listing.json())

    one = await client.get(f"/api/conversations/{conv_id}")
    assert one.status_code == 200
    assert one.json()["title"] == "Тестовая беседа"


@pytest.mark.asyncio
async def test_patch_and_delete_conversation(client: AsyncClient) -> None:
    """PATCH заголовка и DELETE беседы."""
    created = await client.post("/api/conversations", json={})
    conv_id = created.json()["id"]

    patched = await client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": "Переименована"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Переименована"

    deleted = await client.delete(f"/api/conversations/{conv_id}")
    assert deleted.status_code == 204

    missing = await client.get(f"/api/conversations/{conv_id}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_set_default_preset(client: AsyncClient) -> None:
    """POST set-default переключает is_default."""
    presets = (await client.get("/api/presets")).json()
    image_gen = next(p for p in presets if p["slug"] == "image_gen")

    response = await client.post(f"/api/presets/{image_gen['id']}/set-default")
    assert response.status_code == 200
    assert response.json()["is_default"] is True

    presets_after = (await client.get("/api/presets")).json()
    defaults = [p for p in presets_after if p["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["slug"] == "image_gen"
