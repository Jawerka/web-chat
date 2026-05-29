"""
Черновик ответа ассистента во время стрима — сохранение в БД для перезагрузки страницы.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.turn_realtime import turn_realtime
from app.config import settings
from app.db.models import MessageRole
from app.db.repositories import ConversationRepository, MessageRepository
from app.services.turn_db import open_turn_session
from app.services.turn_status import STREAMING, TOOL_RUNNING, patch_active_turn_phase

logger = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


class AssistantStreamDraft:
    """Накопление текста стрима с периодической записью в БД (короткие сессии)."""

    def __init__(
        self,
        conversation_id: uuid.UUID,
        emit: EventEmitter,
    ) -> None:
        self._conversation_id = conversation_id
        self._emit = emit
        self._draft_id: uuid.UUID | None = None
        self._json_cache: dict[str, Any] = {}
        self._buffer = ""
        self._reasoning_buffer = ""
        self._flush_lock = asyncio.Lock()
        self._pending_flush = False
        self._last_flushed_len = 0
        self._is_discarded = False

    @property
    def message_id(self) -> uuid.UUID | None:
        return self._draft_id

    @property
    def message(self) -> None:
        """Устарело: ORM не хранится между сессиями; используйте message_id."""
        return None

    @property
    def text(self) -> str:
        return self._buffer

    @property
    def reasoning(self) -> str:
        return self._reasoning_buffer

    def _content_json(self) -> dict[str, Any]:
        return dict(self._json_cache)

    async def _ensure_message(self) -> uuid.UUID:
        """Гарантировать черновик assistant в БД."""
        if self._draft_id is not None:
            return self._draft_id
        async with open_turn_session() as session:
            msg_repo = MessageRepository(session)
            conv_repo = ConversationRepository(session)
            stale = await msg_repo.settle_stale_streaming_assistant_messages(
                self._conversation_id,
                keep_message_id=None,
            )
            if stale:
                logger.info(
                    "Снят streaming с %d зависших черновиков (conv=%s)",
                    stale,
                    self._conversation_id,
                )
            initial_json = patch_active_turn_phase(
                {
                    "images": [],
                    "image_asset_ids": [],
                    "streaming": True,
                },
                turn_phase=STREAMING,
                legacy_phase="text",
            )
            self._json_cache = dict(initial_json)
            message = await msg_repo.create(
                conversation_id=self._conversation_id,
                role=MessageRole.ASSISTANT,
                content_text="",
                content_json=initial_json,
            )
            conv = await conv_repo.get_by_id(self._conversation_id)
            if conv is not None:
                await conv_repo.touch(conv)
            await session.commit()
            self._draft_id = message.id
        turn_realtime().set_streaming_message(self._conversation_id, self._draft_id)
        await self._emit(
            "assistant_draft",
            {"assistant_message_id": str(self._draft_id)},
        )
        logger.info("Черновик ответа %s создан", self._draft_id)
        return self._draft_id

    async def _update_content_json(self, patch: dict[str, Any]) -> None:
        if self._draft_id is None or self._is_discarded:
            return
        async with self._flush_lock:
            if self._draft_id is None or self._is_discarded:
                return
            merged = self._content_json()
            merged.update(patch)
            self._json_cache = merged
            async with open_turn_session() as session:
                msg_repo = MessageRepository(session)
                conv_repo = ConversationRepository(session)
                message = await msg_repo.get_by_id(self._draft_id)
                if message is None:
                    return
                await msg_repo.update_content(
                    message,
                    content_text=self._buffer,
                    content_json=merged,
                )
                conv = await conv_repo.get_by_id(self._conversation_id)
                if conv is not None:
                    await conv_repo.touch(conv)
                await session.commit()

    async def on_reasoning_delta(self, chunk: str) -> None:
        if not chunk:
            return
        self._reasoning_buffer += chunk
        if self._draft_id is None:
            await self._ensure_message()
        await self._emit("reasoning_delta", {"content": chunk})

    async def on_delta(self, chunk: str) -> None:
        if not chunk:
            return
        self._buffer += chunk
        if self._draft_id is None:
            await self._ensure_message()
        elif self._content_json().get("phase") == "tool":
            await self._update_content_json(
                patch_active_turn_phase(
                    {"active_tool": None},
                    turn_phase=STREAMING,
                    legacy_phase="text",
                ),
            )
        await self._emit("text_delta", {"content": chunk})
        self._pending_flush = True
        asyncio.create_task(self._debounced_flush())
        if len(self._buffer) - self._last_flushed_len >= settings.stream_flush_min_bytes:
            asyncio.create_task(self._flush_safe())

    async def _debounced_flush(self) -> None:
        await asyncio.sleep(0.35)
        if self._is_discarded or not self._pending_flush:
            return
        self._pending_flush = False
        try:
            await self.flush()
        except Exception:
            logger.exception("AssistantStreamDraft: debounced flush failed")

    async def _flush_safe(self) -> None:
        try:
            await self.flush()
        except Exception:
            logger.exception("AssistantStreamDraft: flush task failed")

    async def flush(self) -> None:
        if self._draft_id is None or self._is_discarded:
            return
        async with self._flush_lock:
            if self._draft_id is None or self._is_discarded:
                return
            async with open_turn_session() as session:
                msg_repo = MessageRepository(session)
                conv_repo = ConversationRepository(session)
                message = await msg_repo.get_by_id(self._draft_id)
                if message is None:
                    return
                await msg_repo.update_content(
                    message,
                    content_text=self._buffer,
                )
                conv = await conv_repo.get_by_id(self._conversation_id)
                if conv is not None:
                    await conv_repo.touch(conv)
                await session.commit()
            self._last_flushed_len = len(self._buffer)

    async def enter_tool_round(self, active_tool: str | None = None) -> None:
        await self._ensure_message()
        cj = self._content_json()
        patch = patch_active_turn_phase(
            {
                "streaming": True,
                "active_tool": active_tool,
                "images": cj.get("images") or [],
                "image_asset_ids": cj.get("image_asset_ids") or [],
            },
            turn_phase=TOOL_RUNNING,
            legacy_phase="tool",
        )
        await self._update_content_json(patch)
        if self._draft_id is not None:
            turn_realtime().set_streaming_message(self._conversation_id, self._draft_id)
        logger.info(
            "Черновик %s: фаза tool (%s)",
            self._draft_id,
            active_tool,
        )

    async def set_active_tool(self, name: str) -> None:
        if self._draft_id is None:
            return
        await self._update_content_json(
            patch_active_turn_phase(
                {"active_tool": name},
                turn_phase=TOOL_RUNNING,
                legacy_phase="tool",
            ),
        )

    async def add_images(
        self,
        urls: list[str],
        asset_ids: list[str] | None = None,
    ) -> None:
        from app.services.message_builder import canonical_stored_image_urls

        canonical = canonical_stored_image_urls(urls, asset_ids)
        if self._draft_id is None or not canonical and not asset_ids:
            return
        cj = self._content_json()
        aids = list(cj.get("image_asset_ids") or [])
        if asset_ids:
            for aid in asset_ids:
                if aid not in aids:
                    aids.append(aid)
        images = canonical_stored_image_urls(
            list(cj.get("images") or []) + canonical,
            aids,
        )
        await self._update_content_json(
            {
                "images": images,
                "image_asset_ids": aids,
                "streaming": True,
            }
        )
