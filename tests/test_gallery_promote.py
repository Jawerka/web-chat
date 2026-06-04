"""Promote generation/disk → галерея загрузок."""

from __future__ import annotations

import base64

import pytest
from httpx import AsyncClient

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_promote_generation_to_uploads(client: AsyncClient) -> None:
    from app.db import session as db_session
    from app.db.models import GalleryKind
    from app.services.gallery_owner import require_gallery_owner_user
    from app.services.media_service import MediaService

    async with db_session.async_session_factory() as session:
        user = await require_gallery_owner_user(session, None)
        media = MediaService(session)
        asset = await media.create_from_bytes(
            MINIMAL_PNG,
            "image/png",
            original_name="gen.png",
        )
        asset.gallery_kind = GalleryKind.GENERATION.value
        asset.owner_user_id = user.id
        await session.commit()
        aid = asset.id

    r = await client.post(f"/api/gallery/{aid}/promote-to-uploads")
    assert r.status_code == 200
    upload_id = r.json()["upload_id"]
    assert upload_id != str(aid)

    r2 = await client.get("/api/gallery/uploads")
    assert upload_id in {i["id"] for i in r2.json()["images"]}


@pytest.mark.asyncio
async def test_promote_already_upload_rejected(client: AsyncClient) -> None:
    files = {"files": ("up.png", MINIMAL_PNG, "image/png")}
    r = await client.post("/api/gallery/uploads", files=files)
    assert r.status_code == 200
    upload_id = r.json()["items"][0]["id"]

    r2 = await client.post(f"/api/gallery/{upload_id}/promote-to-uploads")
    assert r2.status_code == 400
