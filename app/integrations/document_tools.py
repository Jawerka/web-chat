"""
MCP-инструменты для работы с документами.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastmcp import FastMCP

from app.config import settings
from app.db.session import async_session_factory
from app.services.attachment_service import AttachmentService

logger = logging.getLogger(__name__)


async def _extract_text_async(attachment_id: str, max_chars: int) -> str:
    """Async-обёртка извлечения текста с сессией БД."""
    try:
        aid = uuid.UUID(attachment_id)
    except ValueError as exc:
        raise ValueError(f"Некорректный attachment_id: {attachment_id}") from exc

    async with async_session_factory() as session:
        service = AttachmentService(session)
        try:
            text = await service.extract_text(aid, max_chars=max_chars)
        except ValueError as exc:
            return f"Ошибка extract_text: {exc}"
        await session.commit()
        return text


def register_document_tools(mcp: FastMCP) -> None:
    """Зарегистрировать extract_text на MCP-сервере."""

    @mcp.tool()
    def extract_text(attachment_id: str, max_chars: int = 50000) -> str:
        """
        Извлечь текст из загруженного файла (PDF, DOCX, TXT, изображение с OCR).

        Args:
            attachment_id: UUID вложения из POST /api/upload.
            max_chars: Максимум символов в ответе.
        """
        logger.info("MCP extract_text attachment_id=%s", attachment_id)
        limit = max_chars if max_chars > 0 else settings.max_extract_chars
        return asyncio.run(_extract_text_async(attachment_id, limit))
