"""Тесты REST API бесед и пресетов (этап 2)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.helpers import api_create_conversation, record_created_conversation


@pytest.mark.asyncio
async def test_presets_seeded(client: AsyncClient) -> None:
    """После старта доступны seed-пресеты, включая img2img."""
    response = await client.get("/api/presets")
    assert response.status_code == 200
    presets = response.json()
    assert len(presets) >= 4
    slugs = {p["slug"] for p in presets}
    assert slugs >= {"default", "image_gen", "img2img", "document_analysis"}
    defaults = [p for p in presets if p["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["slug"] == "default"


@pytest.mark.asyncio
async def test_create_conversation_without_preset_id(client: AsyncClient) -> None:
    """POST /api/conversations без preset_id использует default."""
    response = await client.post("/api/conversations", json={"title": ""})
    assert response.status_code == 201
    data = record_created_conversation(response.json())
    assert data["title"] == "Новая беседа"
    assert "id" in data
    assert "preset_id" in data

    presets = (await client.get("/api/presets")).json()
    default_preset = next(p for p in presets if p["is_default"])
    assert data["preset_id"] == default_preset["id"]


@pytest.mark.asyncio
async def test_create_conversation_visible_immediately(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """GET сразу после POST — беседа уже в БД (без гонки с commit dependency)."""
    created = await client.post(
        "/api/conversations",
        json={"title": test_conv_title},
    )
    assert created.status_code == 201
    conv_id = record_created_conversation(created.json())["id"]

    one = await client.get(f"/api/conversations/{conv_id}")
    assert one.status_code == 200
    assert one.json()["title"] == test_conv_title

    listing = await client.get("/api/conversations")
    assert any(c["id"] == conv_id for c in listing.json())


@pytest.mark.asyncio
async def test_list_and_get_conversation(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """Список бесед и GET по id."""
    created = await api_create_conversation(client, test_conv_title)
    conv_id = created["id"]

    listing = await client.get("/api/conversations")
    assert listing.status_code == 200
    assert any(c["id"] == conv_id for c in listing.json())

    one = await client.get(f"/api/conversations/{conv_id}")
    assert one.status_code == 200
    assert one.json()["title"] == test_conv_title


@pytest.mark.asyncio
async def test_patch_and_delete_conversation(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """PATCH заголовка и DELETE беседы."""
    created = await api_create_conversation(client, test_conv_title)
    conv_id = created["id"]

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
async def test_patch_conversation_preset_to_img2img(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """PATCH preset_id на img2img (регрессия: UUID с дефисами в SQLite)."""
    presets = (await client.get("/api/presets")).json()
    img2img = next(p for p in presets if p["slug"] == "img2img")

    created = await api_create_conversation(client, test_conv_title)
    conv_id = created["id"]

    patched = await client.patch(
        f"/api/conversations/{conv_id}",
        json={"preset_id": img2img["id"]},
    )
    assert patched.status_code == 200
    assert patched.json()["preset_id"] == img2img["id"]


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
