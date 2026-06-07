"""API галереи загрузок."""

from __future__ import annotations

import base64

import pytest
from httpx import AsyncClient

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_uploads_list_and_upload(client: AsyncClient) -> None:
    files = {"files": ("up.png", MINIMAL_PNG, "image/png")}
    r = await client.post("/api/gallery/uploads", files=files)
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 1
    upload_id = data["items"][0]["id"]

    r2 = await client.get("/api/gallery/uploads")
    assert r2.status_code == 200
    ids = {i["id"] for i in r2.json()["images"]}
    assert upload_id in ids

    r3 = await client.get(f"/api/gallery/uploads/{upload_id}")
    assert r3.status_code == 200

    r4 = await client.get(f"/media/asset/{upload_id}")
    assert r4.status_code == 200
    assert r4.content == MINIMAL_PNG

    r5 = await client.delete(f"/api/gallery/uploads/{upload_id}")
    assert r5.status_code == 204


@pytest.mark.asyncio
async def test_uploads_cross_user_forbidden(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "multi_user_enabled", True)
    alice = {"X-Web-Chat-User": "gallery-alice"}
    bob = {"X-Web-Chat-User": "gallery-bob"}

    files = {"files": ("up.png", MINIMAL_PNG, "image/png")}
    r = await client.post("/api/gallery/uploads", files=files, headers=alice)
    assert r.status_code == 200
    upload_id = r.json()["items"][0]["id"]

    r_del = await client.delete(f"/api/gallery/uploads/{upload_id}", headers=bob)
    assert r_del.status_code == 403

    r_media = await client.get(f"/media/asset/{upload_id}", headers=bob)
    assert r_media.status_code == 403

    r_ok = await client.get(f"/media/asset/{upload_id}", headers=alice)
    assert r_ok.status_code == 200
    assert r_ok.content == MINIMAL_PNG


@pytest.mark.asyncio
async def test_uploads_reorder_persists(client: AsyncClient) -> None:
    ids: list[str] = []
    for i in range(3):
        files = {"files": (f"up{i}.png", MINIMAL_PNG, "image/png")}
        r = await client.post("/api/gallery/uploads", files=files)
        assert r.status_code == 200
        ids.append(r.json()["items"][0]["id"])

    reversed_ids = list(reversed(ids))
    r_reorder = await client.post(
        "/api/gallery/uploads/reorder",
        json={"ids": reversed_ids},
    )
    assert r_reorder.status_code == 200

    r_list = await client.get("/api/gallery/uploads")
    assert r_list.status_code == 200
    listed = [i["id"] for i in r_list.json()["images"] if i["id"] in ids]
    assert listed == reversed_ids

    # После reorder новая загрузка не должна падать (max_upload_sort_order).
    r_after = await client.post(
        "/api/gallery/uploads",
        files={"files": ("after-reorder.png", MINIMAL_PNG, "image/png")},
    )
    assert r_after.status_code == 200
