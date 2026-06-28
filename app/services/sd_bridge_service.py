"""
Gallery → SD WebUI bridge: push PNG + infotext to SD queue; optional token GET for legacy.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import MediaAsset
from app.db.repositories import MediaAssetRepository, UserRepository
from app.integrations.media_utils import is_image_mime, resolve_generated_file
from app.services.gallery_owner import assert_gallery_media_access, require_gallery_owner_user
from app.services.media_asset_crypto import decrypt_asset_data
from app.services.request_user import RequestUser
from app.services.sd_infotext import build_infotext_from_fields, infotext_from_png_bytes
from app.services.sd_metadata import extract_sd_metadata_from_bytes

logger = logging.getLogger(__name__)

BRIDGE_TOKEN_TTL_SEC = 60
BRIDGE_TOKEN_SALT = "webchat-sd-bridge-v1"
BRIDGE_TOKEN_VERSION = 1

BridgeSource = Literal["db", "disk"]


@dataclass(frozen=True, slots=True)
class BridgeImportPayload:
    image_base64: str
    infotext: str
    filename: str
    mime: str


@dataclass(slots=True)
class _PendingImport:
    jti: uuid.UUID
    user_id: uuid.UUID
    source: BridgeSource
    asset_key: str
    created_at: float


_lock = threading.Lock()
_pending: dict[uuid.UUID, _PendingImport] = {}


def _bridge_secret() -> str:
    secret = (settings.auth_secret or "").strip()
    if len(secret) >= 32:
        return secret
    if not settings.auth_enabled:
        return "web-chat-sd-bridge-dev-secret-32chars!!"
    raise RuntimeError("AUTH_SECRET required for SD bridge when auth is enabled")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_bridge_secret(), salt=BRIDGE_TOKEN_SALT)


def _purge_expired_locked(now: float) -> None:
    cutoff = now - BRIDGE_TOKEN_TTL_SEC
    stale = [k for k, v in _pending.items() if v.created_at < cutoff]
    for key in stale:
        _pending.pop(key, None)


def _normalize_sd_url(url: str | None) -> str:
    base = (url or settings.sd_webui_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("SD WebUI URL is not configured")
    return base


def create_sd_open_url(token: str, *, sd_webui_url: str | None = None) -> str:
    base = _normalize_sd_url(sd_webui_url)
    return f"{base}/?web_chat_import={quote(token, safe='')}"


def _infotext_for_asset(asset: MediaAsset, data: bytes) -> str:
    if asset.sd_prompt or asset.sd_negative or asset.sd_params:
        text = build_infotext_from_fields(
            prompt=asset.sd_prompt or "",
            negative=asset.sd_negative or "",
            params=asset.sd_params or "",
        )
        if text:
            return text
    from_png = infotext_from_png_bytes(data)
    if from_png:
        return from_png
    meta = extract_sd_metadata_from_bytes(data)
    if meta and meta.has_metadata:
        from app.services.sd_infotext import build_a1111_infotext

        return build_a1111_infotext(meta)
    raise ValueError("no SD metadata")


async def _load_db_asset_bytes(
    session: AsyncSession,
    asset_id: uuid.UUID,
    *,
    owner_user_id: uuid.UUID,
) -> tuple[bytes, str, str, MediaAsset]:
    repo = MediaAssetRepository(session)
    asset = await repo.get_by_id(asset_id)
    if asset is None:
        raise FileNotFoundError("asset not found")
    if asset.owner_user_id is not None and asset.owner_user_id != owner_user_id:
        raise PermissionError("forbidden")
    owner = await UserRepository(session).get_by_id(owner_user_id)
    if owner is None:
        raise PermissionError("forbidden")
    data = decrypt_asset_data(asset, owner)
    filename = asset.original_name or f"{asset.id}.png"
    return data, asset.mime_type, filename, asset


async def _load_import_payload_for_owner(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    asset_id: str,
    source: BridgeSource,
) -> BridgeImportPayload:
    source_norm: BridgeSource = "disk" if source == "disk" else "db"
    asset_key = asset_id.strip()
    if not asset_key:
        raise ValueError("asset_id required")

    if source_norm == "db":
        asset_uuid = uuid.UUID(asset_key)
        data, mime, filename, asset = await _load_db_asset_bytes(
            session,
            asset_uuid,
            owner_user_id=owner_user_id,
        )
        infotext = _infotext_for_asset(asset, data)
    else:
        path = resolve_generated_file(asset_key)
        if not path.is_file():
            raise FileNotFoundError("file not found")
        data = path.read_bytes()
        mime = "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
        filename = path.name
        infotext = infotext_from_png_bytes(data)
        if not infotext:
            raise ValueError("no SD metadata")

    if not is_image_mime(mime):
        mime = "image/png"

    return BridgeImportPayload(
        image_base64=base64.b64encode(data).decode("ascii"),
        infotext=infotext,
        filename=filename,
        mime=mime,
    )


async def load_import_payload(
    session: AsyncSession,
    *,
    request_user: RequestUser | None,
    asset_id: str,
    source: BridgeSource,
) -> BridgeImportPayload:
    """Load PNG bytes + infotext for gallery asset (no token)."""
    source_norm: BridgeSource = "disk" if source == "disk" else "db"
    asset_key = asset_id.strip()
    if not asset_key:
        raise ValueError("asset_id required")

    owner = await require_gallery_owner_user(session, request_user)

    if source_norm == "db":
        try:
            asset_uuid = uuid.UUID(asset_key)
        except ValueError as exc:
            raise ValueError("invalid asset_id") from exc
        repo = MediaAssetRepository(session)
        asset = await repo.get_by_id(asset_uuid)
        if asset is None:
            raise FileNotFoundError("asset not found")
        await assert_gallery_media_access(session, asset, request_user)
        if not is_image_mime(asset.mime_type):
            raise ValueError("asset is not an image")
    else:
        path = resolve_generated_file(asset_key)
        if not path.is_file():
            raise FileNotFoundError("file not found")

    return await _load_import_payload_for_owner(
        session,
        owner_user_id=owner.id,
        asset_id=asset_key,
        source=source_norm,
    )


async def push_payload_to_sd_webui(
    payload: BridgeImportPayload,
    *,
    sd_webui_url: str | None = None,
) -> str:
    """POST payload to SD extension queue. Returns normalized SD base URL."""
    base = _normalize_sd_url(sd_webui_url)
    url = f"{base}/web-chat-bridge/push"
    body = {
        "image_base64": payload.image_base64,
        "infotext": payload.infotext,
        "filename": payload.filename,
        "mime": payload.mime,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        raise ConnectionError(f"SD WebUI HTTP {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise ConnectionError(f"SD WebUI unreachable at {base}: {exc}") from exc

    logger.info(
        "sd_bridge: queued %s → %s",
        payload.filename,
        base,
        extra={"event": "sd_bridge_queued", "asset_name": payload.filename, "sd_url": base},
    )
    return base


async def queue_sd_import(
    session: AsyncSession,
    *,
    request_user: RequestUser | None,
    asset_id: str,
    source: BridgeSource,
    sd_webui_url: str | None = None,
) -> dict:
    """POST /api/sd-bridge/import — push PNG+infotext to SD queue (no browser tab)."""
    payload = await load_import_payload(
        session,
        request_user=request_user,
        asset_id=asset_id,
        source=source,
    )
    sd_url = await push_payload_to_sd_webui(payload, sd_webui_url=sd_webui_url)
    return {
        "queued": True,
        "filename": payload.filename,
        "sd_webui_url": sd_url,
    }


async def create_import_token(
    session: AsyncSession,
    *,
    request_user: RequestUser | None,
    asset_id: str,
    source: BridgeSource,
    sd_webui_url: str | None = None,
) -> dict:
    """Legacy: one-time token for GET /api/sd-bridge/import/{token}."""
    source_norm: BridgeSource = "disk" if source == "disk" else "db"
    asset_key = asset_id.strip()
    if not asset_key:
        raise ValueError("asset_id required")

    owner = await require_gallery_owner_user(session, request_user)

    if source_norm == "db":
        try:
            asset_uuid = uuid.UUID(asset_key)
        except ValueError as exc:
            raise ValueError("invalid asset_id") from exc
        repo = MediaAssetRepository(session)
        asset = await repo.get_by_id(asset_uuid)
        if asset is None:
            raise FileNotFoundError("asset not found")
        await assert_gallery_media_access(session, asset, request_user)
        if not is_image_mime(asset.mime_type):
            raise ValueError("asset is not an image")
    else:
        path = resolve_generated_file(asset_key)
        if not path.is_file():
            raise FileNotFoundError("file not found")

    jti = uuid.uuid4()
    now = time.time()
    with _lock:
        _purge_expired_locked(now)
        _pending[jti] = _PendingImport(
            jti=jti,
            user_id=owner.id,
            source=source_norm,
            asset_key=asset_key,
            created_at=now,
        )

    token = _serializer().dumps(
        {
            "v": BRIDGE_TOKEN_VERSION,
            "jti": str(jti),
            "uid": str(owner.id),
            "src": source_norm,
            "aid": asset_key,
        }
    )
    return {
        "token": token,
        "expires_in": BRIDGE_TOKEN_TTL_SEC,
        "sd_open_url": create_sd_open_url(token, sd_webui_url=sd_webui_url),
    }


def _consume_pending(
    jti: uuid.UUID,
    *,
    user_id: uuid.UUID,
    source: BridgeSource,
    asset_key: str,
) -> None:
    with _lock:
        record = _pending.pop(jti, None)
    if record is None:
        raise PermissionError("token expired or already used")
    if record.user_id != user_id or record.source != source or record.asset_key != asset_key:
        raise PermissionError("token mismatch")


def _verify_token(token: str) -> dict:
    try:
        data = _serializer().loads(token, max_age=BRIDGE_TOKEN_TTL_SEC)
    except (BadSignature, SignatureExpired) as exc:
        raise PermissionError("invalid or expired token") from exc
    if not isinstance(data, dict) or data.get("v") != BRIDGE_TOKEN_VERSION:
        raise PermissionError("invalid token payload")
    try:
        jti = uuid.UUID(str(data["jti"]))
        user_id = uuid.UUID(str(data["uid"]))
    except (KeyError, ValueError) as exc:
        raise PermissionError("invalid token payload") from exc
    source = str(data.get("src") or "db")
    if source not in {"db", "disk"}:
        raise PermissionError("invalid token source")
    asset_key = str(data.get("aid") or "").strip()
    if not asset_key:
        raise PermissionError("invalid token asset")
    return {
        "jti": jti,
        "user_id": user_id,
        "source": source,  # type: ignore[return-value]
        "asset_key": asset_key,
    }


async def resolve_import_payload(
    session: AsyncSession,
    token: str,
) -> BridgeImportPayload:
    """GET /api/sd-bridge/import/{token} — legacy one-time fetch for SD extension."""
    verified = _verify_token(token)
    _consume_pending(
        verified["jti"],
        user_id=verified["user_id"],
        source=verified["source"],
        asset_key=verified["asset_key"],
    )

    return await _load_import_payload_for_owner(
        session,
        owner_user_id=verified["user_id"],
        asset_id=verified["asset_key"],
        source=verified["source"],
    )


def reset_bridge_store_for_tests() -> None:
    with _lock:
        _pending.clear()
