"""SD WebUI bridge: one-click import from gallery."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.request_user import RequestUser, get_request_user
from app.services.sd_bridge_service import queue_sd_import, resolve_import_payload

router = APIRouter(tags=["sd-bridge"])


class SdBridgeImportCreate(BaseModel):
    asset_id: str = Field(min_length=1, max_length=255)
    source: Literal["db", "disk"] = "db"
    sd_webui_url: str | None = Field(default=None, max_length=512)


@router.post("/sd-bridge/import")
async def api_sd_bridge_create(
    payload: SdBridgeImportCreate,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    try:
        return await queue_sd_import(
            db,
            request_user=user,
            asset_id=payload.asset_id,
            source=payload.source,
            sd_webui_url=payload.sd_webui_url,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/sd-bridge/import/{token}")
async def api_sd_bridge_fetch(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await resolve_import_payload(db, token)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "image_base64": result.image_base64,
        "infotext": result.infotext,
        "filename": result.filename,
        "mime": result.mime,
    }
