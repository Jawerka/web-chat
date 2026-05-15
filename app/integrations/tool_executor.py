"""
Выполнение инструментов по запросу LLM (in-process).

Возвращает текстовый result для role=tool и список URL изображений.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory
from app.integrations.sd_tools import generate_image
from app.services.attachment_service import AttachmentService
from app.services.media_service import MediaService, parse_asset_id_from_url

logger = logging.getLogger(__name__)

IMAGE_URL_RE = re.compile(
    r"URL:\s*(\S+)|(/media/(?:asset/[0-9a-fA-F-]{36}|generated/[^\s\)]+\.(?:png|jpg|jpeg|webp|gif)))",
    re.IGNORECASE,
)


@dataclass
class ToolResult:
    """Результат вызова инструмента."""

    content: str
    image_urls: list[str]
    image_asset_ids: list[str] | None = None
    url_rewrites: dict[str, str] | None = None


class ToolExecutor:
    """Маршрутизатор вызовов tools (без HTTP на свой MCP)."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> None:
        """Опциональная async-сессия БД (для оркестратора с открытой транзакцией)."""
        self._session = session
        self._conversation_id = conversation_id

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Выполнить инструмент по имени.

        Raises:
            ValueError: Неизвестный инструмент.
        """
        logger.info("Вызов инструмента %s args=%s", name, list(arguments.keys()))
        if name == "generate_image":
            return await self._generate_image(arguments)
        if name == "extract_text":
            text = await self._extract_text(arguments)
            return ToolResult(content=text, image_urls=[])
        raise ValueError(f"Неизвестный инструмент: {name}")

    async def _generate_image(self, arguments: dict[str, Any]) -> ToolResult:
        """generate_image в thread pool; затем перенос в БД."""
        sig = inspect.signature(generate_image)
        filtered = {
            k: v
            for k, v in arguments.items()
            if k in sig.parameters and v is not None
        }
        t0 = time.monotonic()
        text = await asyncio.to_thread(generate_image, **filtered)
        sd_elapsed = time.monotonic() - t0
        urls = self._parse_urls(text)
        logger.info(
            "generate_image SD завершён за %.1fs, найдено URL: %d",
            sd_elapsed,
            len(urls),
        )
        url_map: dict[str, str] = {}
        asset_ids: list[str] = []
        if self._session is not None:
            t1 = time.monotonic()
            media = MediaService(self._session)
            ingested, url_map, raw_ids = await media.ingest_sd_output_files(
                text,
                conversation_id=self._conversation_id,
            )
            ingest_elapsed = time.monotonic() - t1
            if ingested:
                urls = ingested
                asset_ids = [str(i) for i in raw_ids]
                logger.info(
                    "generate_image ingest OK за %.1fs: %d asset(s)",
                    ingest_elapsed,
                    len(asset_ids),
                )
            else:
                logger.warning(
                    "generate_image ingest пустой за %.1fs (parsed urls=%d)",
                    ingest_elapsed,
                    len(urls),
                )
                urls = await media.normalize_image_urls(
                    urls,
                    conversation_id=self._conversation_id,
                )
                for u in urls:
                    aid = parse_asset_id_from_url(u)
                    if aid:
                        asset_ids.append(str(aid))
        else:
            logger.warning("generate_image: нет сессии БД — ingest пропущен")
        logger.info(
            "generate_image итог за %.1fs: urls=%d assets=%d",
            time.monotonic() - t0,
            len(urls),
            len(asset_ids),
        )
        return ToolResult(
            content=text,
            image_urls=urls,
            image_asset_ids=asset_ids,
            url_rewrites=url_map,
        )

    async def _extract_text(self, arguments: dict[str, Any]) -> str:
        """Извлечь текст вложения (in-process, с кэшем в БД)."""
        raw_id = arguments.get("attachment_id")
        if not raw_id:
            return "Ошибка: не указан attachment_id"
        try:
            attachment_id = uuid.UUID(str(raw_id))
        except ValueError:
            return f"Ошибка: некорректный attachment_id: {raw_id}"

        max_chars = int(arguments.get("max_chars") or settings.max_extract_chars)

        if self._session is not None:
            service = AttachmentService(self._session)
            try:
                return await service.extract_text(attachment_id, max_chars=max_chars)
            except ValueError as exc:
                return f"Ошибка extract_text: {exc}"

        async with async_session_factory() as session:
            service = AttachmentService(session)
            try:
                text = await service.extract_text(attachment_id, max_chars=max_chars)
            except ValueError as exc:
                return f"Ошибка extract_text: {exc}"
            await session.commit()
            return text

    @staticmethod
    def _parse_urls(tool_output: str) -> list[str]:
        """Извлечь URL картинок из текстового отчёта."""
        urls: list[str] = []
        for match in IMAGE_URL_RE.finditer(tool_output):
            url = match.group(1) or match.group(2)
            if url and url not in urls:
                urls.append(url)
        return urls
