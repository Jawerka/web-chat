"""Тесты PATCH пресетов."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_patch_preset_system_prompt(client: AsyncClient) -> None:
    """PATCH /api/presets/{id} обновляет system_prompt."""
    presets = (await client.get("/api/presets")).json()
    target = presets[0]
    new_prompt = "Тестовый системный промпт для пресета."

    response = await client.patch(
        f"/api/presets/{target['id']}",
        json={"system_prompt": new_prompt},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == target["id"]
    assert data["system_prompt"] == new_prompt

    listed = (await client.get("/api/presets")).json()
    updated = next(p for p in listed if p["id"] == target["id"])
    assert updated["system_prompt"] == new_prompt


@pytest.mark.asyncio
async def test_patch_preset_not_found(client: AsyncClient) -> None:
    """PATCH несуществующего пресета — 404."""
    response = await client.patch(
        "/api/presets/00000000-0000-0000-0000-000000000099",
        json={"system_prompt": "x"},
    )
    assert response.status_code == 404
