"""SD bridge API: queue push + legacy token fetch."""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image, PngImagePlugin

from app.services.sd_bridge_service import (
    create_import_token,
    reset_bridge_store_for_tests,
    resolve_import_payload,
)
from app.services.media_registry import MediaRegistry


def _png_with_parameters(params: str) -> bytes:
    img = Image.new("RGB", (1, 1), color=(0, 0, 0))
    buf = io.BytesIO()
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", params)
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _reset_bridge_store() -> None:
    reset_bridge_store_for_tests()
    yield
    reset_bridge_store_for_tests()


@pytest.fixture
def mock_sd_push(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    mock_factory = MagicMock(return_value=mock_client)
    monkeypatch.setattr("app.services.sd_bridge_service.httpx.AsyncClient", mock_factory)
    return mock_client.post


@pytest.mark.asyncio
async def test_sd_bridge_queue_push_db_asset(client, mock_sd_push) -> None:
    from app.db import session as db_session

    params = "a bridge cat\nNegative prompt: blurry\nSteps: 20, Sampler: Euler, Seed: 42"
    png = _png_with_parameters(params)

    async with db_session.async_session_factory() as session:
        reg = await MediaRegistry(session).register_image(
            png,
            "image/png",
            original_name="bridge-test.png",
            gallery_kind="generation",
        )
        await session.commit()
        asset_id = str(reg.id)

    create_res = await client.post(
        "/api/sd-bridge/import",
        json={"asset_id": asset_id, "source": "db", "sd_webui_url": "http://192.168.88.52:7860"},
    )
    assert create_res.status_code == 200
    body = create_res.json()
    assert body["queued"] is True
    assert body["filename"]
    assert body["sd_webui_url"] == "http://192.168.88.52:7860"
    assert mock_sd_push.await_count == 1
    pushed = mock_sd_push.await_args.args[0]
    assert pushed.endswith("/web-chat-bridge/push")
    payload = mock_sd_push.await_args.kwargs["json"]
    assert payload["image_base64"]
    assert "bridge cat" in payload["infotext"]


@pytest.mark.asyncio
async def test_sd_bridge_legacy_token_fetch(client) -> None:
    from app.db import session as db_session

    params = "legacy cat\nNegative prompt: blurry\nSteps: 20, Seed: 42"
    png = _png_with_parameters(params)

    async with db_session.async_session_factory() as session:
        reg = await MediaRegistry(session).register_image(
            png,
            "image/png",
            original_name="legacy.png",
            gallery_kind="generation",
        )
        await session.commit()
        asset_id = str(reg.id)
        token_payload = await create_import_token(
            session,
            request_user=None,
            asset_id=asset_id,
            source="db",
        )
        await session.commit()
        token = token_payload["token"]

    fetch_res = await client.get(f"/api/sd-bridge/import/{token}")
    assert fetch_res.status_code == 200
    payload = fetch_res.json()
    assert payload["filename"]
    assert payload["image_base64"]
    assert "legacy cat" in payload["infotext"]

    again = await client.get(f"/api/sd-bridge/import/{token}")
    assert again.status_code == 403


@pytest.mark.asyncio
async def test_sd_bridge_fetch_disk_file(client, tmp_path, monkeypatch, mock_sd_push) -> None:
    from app.integrations import media_utils

    gen = tmp_path / "generated"
    gen.mkdir()
    monkeypatch.setattr(media_utils, "GENERATED_ROOT", gen)

    params = "disk item\nNegative prompt: x\nSteps: 10, Seed: 1"
    name = "disk-bridge.png"
    (gen / name).write_bytes(_png_with_parameters(params))

    create_res = await client.post(
        "/api/sd-bridge/import",
        json={"asset_id": name, "source": "disk"},
    )
    assert create_res.status_code == 200
    body = create_res.json()
    assert body["queued"] is True
    assert body["filename"] == name
    pushed = mock_sd_push.await_args.kwargs["json"]
    assert "disk item" in pushed["infotext"]


@pytest.mark.asyncio
async def test_sd_bridge_fetch_without_session_when_auth_enabled(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy GET /api/sd-bridge/import/{token} без cookie."""
    from httpx import ASGITransport, AsyncClient

    from app.config import settings
    from app.db import session as db_session
    from app.db.models import UserRole
    from app.db.repositories import UserRepository
    from app.main import create_app
    from app.security.passwords import hash_password
    from app.services.request_user import RequestUser

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_secret", "test-auth-secret-key-32chars-minimum!!")

    params = "auth bypass cat\nNegative prompt: x\nSteps: 12, Seed: 1"
    png = _png_with_parameters(params)

    async with db_session.async_session_factory() as session:
        user = await UserRepository(session).create_user(
            login="bridgeuser",
            slug="bridgeuser",
            display_name="Bridge",
            password_hash=hash_password("secret"),
            role=UserRole.USER,
        )
        reg = await MediaRegistry(session).register_image(
            png,
            "image/png",
            original_name="bridge-auth.png",
            gallery_kind="generation",
        )
        await session.commit()
        asset_id = str(reg.id)
        request_user = RequestUser(
            id=user.id,
            slug=user.slug,
            display_name=user.display_name,
            login=user.login,
            role=user.role.value if hasattr(user.role, "value") else str(user.role),
        )

        token_payload = await create_import_token(
            session,
            request_user=request_user,
            asset_id=asset_id,
            source="db",
        )
        token = token_payload["token"]

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        fetch_res = await anon.get(f"/api/sd-bridge/import/{token}")

    assert fetch_res.status_code == 200
    assert "auth bypass cat" in fetch_res.json()["infotext"]


@pytest.mark.asyncio
async def test_sd_bridge_unknown_asset_404(client, mock_sd_push) -> None:
    res = await client.post(
        "/api/sd-bridge/import",
        json={"asset_id": str(uuid.uuid4()), "source": "db"},
    )
    assert res.status_code == 404
    assert mock_sd_push.await_count == 0
