"""SD ingest: gallery_kind=generation даже при conversation_id."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.db.models import GalleryKind
from app.integrations import media_utils
from app.services.gallery_service import list_gallery_images

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_ingest_sd_sets_generation_gallery_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gen = tmp_path / "generated"
    gen.mkdir()
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)

    from app.db import session as db_session
    from app.db.repositories import ConversationRepository
    from app.services.media_service import MediaService

    filename = "sd_out.png"
    (gen / filename).write_bytes(MINIMAL_PNG)
    tool_text = f"Изображение 1:\n  URL: /media/generated/{filename}\n"

    async with db_session.async_session_factory() as session:
        conv = await ConversationRepository(session).create(title="t")
        await session.commit()
        conv_id = conv.id

        media = MediaService(session)
        ingested, _url_map, asset_ids = await media.ingest_sd_output_files(
            tool_text,
            conversation_id=conv_id,
        )
        await session.commit()

        assert len(ingested) == 1
        assert len(asset_ids) == 1

        from app.services.media_registry import MediaRegistry

        asset = await MediaRegistry(session).get_by_id(asset_ids[0])
        assert asset is not None
        assert asset.gallery_kind == GalleryKind.GENERATION.value
        assert asset.conversation_id == conv_id

        items = await list_gallery_images(session, limit=50)
        ids = {i.id for i in items}
        assert str(asset_ids[0]) in ids
