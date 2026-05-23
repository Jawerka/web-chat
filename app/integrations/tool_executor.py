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
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import AttachmentRepository, MessageRepository
from app.db.session import async_session_factory
from app.services.message_builder import _public_url_from_image_part
from app.integrations.media_utils import (
    parse_asset_id_from_url,
    parse_upload_from_url,
    resolve_trusted_generated_source,
    resolve_upload_file,
)
from app.integrations.sd_progress import fetch_sd_progress
from app.integrations.sd_tools import generate_image, get_gallery, img2img, upscale_images
from app.services.attachment_service import AttachmentService
from app.services.job_queue import JobCancelled, heavy_job_queue
from app.services.media_service import MediaService
from app.services.user_progress import (
    STAGE_SAVE_MEDIA,
    build_progress,
    is_sd_tool,
    progress_from_sd_snapshot,
    stage_for_tool,
)

ProgressEmit = Callable[[dict[str, Any]], Awaitable[None]]

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
        sd_webui_url: str | None = None,
        source_user_message_id: uuid.UUID | None = None,
        cancel_event: asyncio.Event | None = None,
        emit_progress: ProgressEmit | None = None,
    ) -> None:
        """Опциональная async-сессия БД (для оркестратора с открытой транзакцией)."""
        self._session = session
        self._conversation_id = conversation_id
        self._sd_webui_url = sd_webui_url
        self._source_user_message_id = source_user_message_id
        self._cancel_event = cancel_event
        self._emit_progress = emit_progress
        # Закреплённый init из user-сообщения на весь ход (несколько img2img подряд).
        self._pinned_user_init: tuple[bytes, str] | None = None

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Выполнить инструмент по имени.

        Raises:
            ValueError: Неизвестный инструмент.
        """
        logger.info(
            "Вызов инструмента %s args=%s conv=%s",
            name,
            list(arguments.keys()),
            self._conversation_id or "-",
        )
        if name == "generate_image":
            return await self._run_sd_image_tool(generate_image, arguments, name)
        if name == "img2img":
            return await self._img2img(arguments)
        if name == "upscale_images":
            return await self._run_sd_image_tool(upscale_images, arguments, name)
        if name == "get_gallery":
            await self._emit_tool_progress(name)
            text = await heavy_job_queue.run_sync(
                get_gallery,
                limit=int(arguments.get("limit") or 20),
                cancel_event=self._cancel_event,
                operation="get_gallery",
            )
            return ToolResult(content=text, image_urls=[])
        if name == "extract_text":
            await self._emit_tool_progress(name)
            text = await self._extract_text(arguments)
            return ToolResult(content=text, image_urls=[])
        raise ValueError(f"Неизвестный инструмент: {name}")

    async def _emit_tool_progress(self, tool_name: str) -> None:
        if self._emit_progress is None:
            return
        await self._emit_progress(
            build_progress(stage_for_tool(tool_name), tool=tool_name),
        )

    async def _load_init_image(
        self,
        url_or_path: str | None = None,
        *,
        attachment_id: uuid.UUID | None = None,
    ) -> tuple[bytes, str]:
        """
        Загрузить исходник для img2img.

        Источники: MediaAsset (/media/asset/…), upload (/media/uploads/…),
        generated (/media/generated/… или имя файла), attachment_id.
        """
        if attachment_id is not None:
            if self._session is None:
                raise ValueError("Нет сессии БД для чтения вложения")
            service = AttachmentService(self._session)
            att = await service._repo.get_by_id(attachment_id)
            if att is None:
                raise ValueError(f"Вложение {attachment_id} не найдено")
            if not att.mime_type.startswith("image/"):
                raise ValueError("img2img поддерживает только изображения")
            if att.media_asset_id is not None:
                media = MediaService(self._session)
                result = await media.get_bytes(att.media_asset_id)
                if result is None:
                    raise ValueError(f"Изображение вложения {attachment_id} не найдено в БД")
                data, _mime = result
                return data, att.original_name
            path = AttachmentService.file_path(att)
            return path.read_bytes(), att.original_name

        if not url_or_path or not str(url_or_path).strip():
            raise ValueError("Укажите init_image_url или attachment_id")

        raw = str(url_or_path).strip()
        asset_id = parse_asset_id_from_url(raw)
        if asset_id is not None:
            if self._session is None:
                raise ValueError("Нет сессии БД для чтения /media/asset/…")
            media = MediaService(self._session)
            result = await media.get_bytes(asset_id)
            if result is not None:
                data, _mime = result
                return data, f"{asset_id}.png"
            # LLM часто подставляет UUID вложения в /media/asset/… — пробуем Attachment
            try:
                return await self._load_init_image(attachment_id=asset_id)
            except (ValueError, FileNotFoundError) as exc:
                raise ValueError(
                    f"Изображение asset/{asset_id} не найдено "
                    f"(и как вложение: {exc})",
                ) from exc

        upload = parse_upload_from_url(raw)
        if upload is not None:
            att_id, filename = upload
            path = resolve_upload_file(att_id, filename)
            return path.read_bytes(), filename

        path = resolve_trusted_generated_source(raw)
        return path.read_bytes(), path.name

    async def _resolve_user_message_init(self) -> tuple[bytes, str] | None:
        """
        Исходник img2img из user-сообщения текущего хода.

        1) Attachment с message_id в БД
        2) image_url / asset_id в content_json.parts (старые сообщения без link_to_message)
        """
        if self._session is None or self._source_user_message_id is None:
            return None

        att_repo = AttachmentRepository(self._session)
        attachments = await att_repo.list_for_message(self._source_user_message_id)
        for att in attachments:
            if att.mime_type.startswith("image/"):
                try:
                    return await self._load_init_image(attachment_id=att.id)
                except (ValueError, FileNotFoundError, RuntimeError) as exc:
                    logger.warning(
                        "img2img: вложение %s не загружено: %s",
                        att.id,
                        exc,
                    )

        msg = await MessageRepository(self._session).get_by_id(self._source_user_message_id)
        if msg and msg.content_json and isinstance(msg.content_json.get("parts"), list):
            for part in msg.content_json["parts"]:
                if part.get("type") != "image_url":
                    continue
                url = _public_url_from_image_part(part)
                raw_asset = part.get("asset_id")
                try:
                    if raw_asset:
                        aid = uuid.UUID(str(raw_asset))
                        if self._session is not None:
                            media = MediaService(self._session)
                            result = await media.get_bytes(aid)
                            if result is not None:
                                data, _mime = result
                                return data, f"{aid}.png"
                    if url:
                        return await self._load_init_image(url)
                except (ValueError, FileNotFoundError, RuntimeError) as exc:
                    logger.warning(
                        "img2img: part user-сообщения не загружен: %s",
                        exc,
                    )

        return None

    async def _get_pinned_user_init(self) -> tuple[bytes, str] | None:
        """Исходник user-сообщения: один раз за ход, далее из кэша."""
        if self._pinned_user_init is not None:
            return self._pinned_user_init
        resolved = await self._resolve_user_message_init()
        if resolved is not None:
            self._pinned_user_init = resolved
        return resolved

    async def _img2img(self, arguments: dict[str, Any]) -> ToolResult:
        """img2img с разрешением init_image из asset, upload, generated или attachment_id.

        Если задан ``source_user_message_id``, исходник сначала берётся с сервера из этого
        сообщения (вложения / parts), перезаписывая неверный init от LLM.
        """
        args = dict(arguments)
        init_url = args.pop("init_image_url", None)
        raw_att = args.pop("attachment_id", None)
        logger.info(
            "img2img args: init_image_url=%r attachment_id=%r source_user=%s",
            init_url,
            raw_att,
            self._source_user_message_id,
        )

        att_uuid: uuid.UUID | None = None
        if raw_att:
            try:
                att_uuid = uuid.UUID(str(raw_att))
            except ValueError:
                return ToolResult(
                    content=f"Ошибка img2img: некорректный attachment_id: {raw_att}",
                    image_urls=[],
                )

        init_bytes: bytes | None = None
        init_name = ""
        load_error: str | None = None
        llm_had_init = bool(init_url or att_uuid is not None)

        if self._source_user_message_id is not None:
            server_init = await self._get_pinned_user_init()
            if server_init is not None:
                init_bytes, init_name = server_init
                if llm_had_init:
                    logger.warning(
                        "img2img: init закреплён из user-сообщения %s (%s, %d байт), "
                        "аргументы LLM проигнорированы (url=%r att=%r)",
                        self._source_user_message_id,
                        init_name,
                        len(init_bytes),
                        init_url,
                        raw_att,
                    )
                else:
                    logger.info(
                        "img2img: init закреплён из user-сообщения %s (%s, %d байт)",
                        self._source_user_message_id,
                        init_name,
                        len(init_bytes),
                    )

        if init_bytes is None and llm_had_init:
            try:
                init_bytes, init_name = await self._load_init_image(
                    str(init_url) if init_url else None,
                    attachment_id=att_uuid,
                )
            except (ValueError, FileNotFoundError) as exc:
                load_error = str(exc)
            except RuntimeError as exc:
                load_error = str(exc)

        if init_bytes is None:
            fallback = await self._get_pinned_user_init()
            if fallback is not None:
                init_bytes, init_name = fallback
                logger.info(
                    "img2img: init взят из user-сообщения %s (%s)",
                    self._source_user_message_id,
                    init_name,
                )
            elif not llm_had_init:
                return ToolResult(
                    content="Ошибка: init image not found — укажите init_image_url или прикрепите изображение",
                    image_urls=[],
                )
            else:
                err = load_error or "не удалось загрузить исходник"
                logger.warning(
                    "img2img: пропуск SD — %s (url=%r att=%r user=%s)",
                    err,
                    init_url,
                    raw_att,
                    self._source_user_message_id,
                )
                return ToolResult(
                    content=f"Ошибка img2img: {err}",
                    image_urls=[],
                )
        args["init_image_bytes"] = init_bytes
        args["init_source_name"] = init_name
        try:
            return await self._run_sd_image_tool(img2img, args, "img2img")
        except ValueError as exc:
            logger.warning("img2img: %s", exc)
            return ToolResult(content=f"Ошибка img2img: {exc}", image_urls=[])
        except RuntimeError as exc:
            return ToolResult(content=str(exc), image_urls=[])

    async def _run_sd_image_tool(
        self,
        func: Any,
        arguments: dict[str, Any],
        tool_name: str,
    ) -> ToolResult:
        """SD-инструмент в thread pool; затем ingest в БД."""
        sig = inspect.signature(func)
        filtered = {k: v for k, v in arguments.items() if k in sig.parameters and v is not None}
        if self._sd_webui_url is not None:
            filtered["sd_webui_url"] = self._sd_webui_url
        t0 = time.monotonic()
        poll_stop = asyncio.Event()
        poll_task: asyncio.Task[None] | None = None
        if is_sd_tool(tool_name) and self._emit_progress is not None:
            poll_task = asyncio.create_task(
                self._poll_sd_progress(tool_name, poll_stop),
            )
        try:
            text = await heavy_job_queue.run_sync(
                lambda: func(**filtered),
                cancel_event=self._cancel_event,
                operation=tool_name,
            )
        except JobCancelled:
            return ToolResult(
                content="Генерация отменена",
                image_urls=[],
            )
        finally:
            poll_stop.set()
            if poll_task is not None:
                poll_task.cancel()
                try:
                    await poll_task
                except asyncio.CancelledError:
                    pass
        sd_elapsed = time.monotonic() - t0
        if self._emit_progress is not None and self._session is not None:
            await self._emit_progress(
                build_progress(STAGE_SAVE_MEDIA, tool=tool_name),
            )
        urls = self._parse_urls(text)
        logger.info(
            "%s SD завершён за %.1fs, найдено URL: %d",
            tool_name,
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
                    "%s ingest OK за %.1fs: %d asset(s)",
                    tool_name,
                    ingest_elapsed,
                    len(asset_ids),
                )
            else:
                logger.warning(
                    "%s ingest пустой за %.1fs (parsed urls=%d)",
                    tool_name,
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
            logger.warning("%s: нет сессии БД — ingest пропущен", tool_name)
        logger.info(
            "%s итог за %.1fs: urls=%d assets=%d",
            tool_name,
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

    async def _poll_sd_progress(
        self,
        tool_name: str,
        stop: asyncio.Event,
    ) -> None:
        """Опрос SD WebUI progress пока идёт синхронный txt2img/img2img/upscale."""
        sd_url = self._sd_webui_url
        emit = self._emit_progress
        if emit is None:
            return
        await emit(build_progress(stage_for_tool(tool_name), tool=tool_name, percent=0))
        while not stop.is_set():
            if self._cancel_event is not None and self._cancel_event.is_set():
                break
            try:
                snapshot = await heavy_job_queue.run_sync(
                    lambda: fetch_sd_progress(sd_url),
                    operation="sd_progress_poll",
                )
            except Exception:
                snapshot = None
            if snapshot and snapshot.get("active") and emit is not None:
                await emit(progress_from_sd_snapshot(tool_name, snapshot))
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.35)
                break
            except TimeoutError:
                continue

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
                return await service.extract_text(
                    attachment_id,
                    max_chars=max_chars,
                    cancel_event=self._cancel_event,
                )
            except ValueError as exc:
                return f"Ошибка extract_text: {exc}"

        async with async_session_factory() as session:
            service = AttachmentService(session)
            try:
                text = await service.extract_text(
                    attachment_id,
                    max_chars=max_chars,
                    cancel_event=self._cancel_event,
                )
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
