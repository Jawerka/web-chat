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

import requests
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.diag_logging import log_event
from app.db.models import Attachment, MediaAsset
from app.db.repositories import AttachmentRepository, MessageRepository
from app.db import session as db_session
from app.services.message_builder import _public_url_from_image_part
from app.integrations.media_utils import (
    parse_asset_id_from_url,
    parse_upload_from_url,
    resolve_trusted_generated_source,
    resolve_upload_file,
)
from app.integrations.sd_progress import fetch_sd_progress
from app.integrations.tool_definitions import tool_timeout_seconds
from app.integrations.sd_tools import generate_image, get_gallery, img2img, upscale_images
from app.services.attachment_service import AttachmentService
from app.services.job_queue import JobCancelled, ShutdownInProgress, heavy_job_queue
from app.integrations.runtime_config import resolve_sd_webui_url
from app.integrations.sd_http import SdUnavailableError, sd_interrupt
from app.services.media_service import MediaService
from app.services.user_progress import (
    STAGE_SAVE_MEDIA,
    build_progress,
    is_sd_tool,
    progress_from_sd_snapshot,
    stage_for_tool,
)

ProgressEmit = Callable[[dict[str, Any]], Awaitable[None]]
ImageEmit = Callable[[list[str], list[str] | None], Awaitable[None]]

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
    # Картинки уже отправлены в UI по одной (img2img + emit_image).
    images_streamed: bool = False


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
        emit_image: ImageEmit | None = None,
    ) -> None:
        """Опциональная async-сессия БД (для оркестратора с открытой транзакцией)."""
        self._session = session
        self._conversation_id = conversation_id
        self._sd_webui_url = sd_webui_url
        self._source_user_message_id = source_user_message_id
        self._cancel_event = cancel_event
        self._emit_progress = emit_progress
        self._emit_image = emit_image
        # Закреплённый init из user-сообщения на весь ход (несколько img2img подряд).
        self._pinned_user_init: tuple[bytes, str] | None = None

    async def _with_tool_timeout(self, tool_name: str, coro: Awaitable[Any]) -> Any:
        """Ограничить время выполнения инструмента (P4.1)."""
        return await asyncio.wait_for(coro, timeout=tool_timeout_seconds(tool_name))

    def _tool_timeout_result(self, tool_name: str) -> ToolResult:
        secs = tool_timeout_seconds(tool_name)
        return ToolResult(
            content=f"Превышено время ожидания инструмента {tool_name} ({secs} с)",
            image_urls=[],
        )

    @asynccontextmanager
    async def _borrow_session(self) -> AsyncIterator[AsyncSession]:
        """Короткая сессия БД, если оркестратор не передал свою (P3.1)."""
        if self._session is not None:
            yield self._session
            return
        async with db_session.async_session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Выполнить инструмент по имени.

        Raises:
            ValueError: Неизвестный инструмент.
        """
        log_event(
            logger,
            "tool_invoke",
            f"invoke {name}",
            tool=name,
            arg_keys=list(arguments.keys()),
        )
        if name == "generate_image":
            return await self._run_sd_image_tool(generate_image, arguments, name)
        if name == "img2img":
            return await self._img2img(arguments)
        if name == "upscale_images":
            return await self._run_sd_image_tool(upscale_images, arguments, name)
        if name == "get_gallery":
            await self._emit_tool_progress(name)
            try:
                text = await self._with_tool_timeout(
                    name,
                    heavy_job_queue.run_sync(
                        get_gallery,
                        limit=int(arguments.get("limit") or 20),
                        cancel_event=self._cancel_event,
                        operation="get_gallery",
                    ),
                )
            except TimeoutError:
                return self._tool_timeout_result(name)
            except ShutdownInProgress:
                return ToolResult(
                    content="Сервер завершает работу — запрос прерван.",
                    image_urls=[],
                )
            except JobCancelled:
                return ToolResult(content="Генерация отменена", image_urls=[])
            return ToolResult(content=text, image_urls=[])
        if name == "extract_text":
            await self._emit_tool_progress(name)
            try:
                text = await self._with_tool_timeout(
                    name,
                    self._extract_text(arguments),
                )
            except TimeoutError:
                return self._tool_timeout_result(name)
            return ToolResult(content=text, image_urls=[])
        raise ValueError(f"Неизвестный инструмент: {name}")

    async def _emit_tool_progress(self, tool_name: str) -> None:
        if self._emit_progress is None:
            return
        await self._emit_progress(
            build_progress(stage_for_tool(tool_name), tool=tool_name),
        )

    def _assert_attachment_scope(self, attachment: Attachment) -> None:
        """Вложение доступно только в текущей беседе (или ещё не привязано)."""
        if self._conversation_id is None:
            return
        if attachment.conversation_id is None:
            return
        if attachment.conversation_id != self._conversation_id:
            raise ValueError(f"Вложение {attachment.id} принадлежит другой беседе")

    def _assert_media_asset_scope(self, asset: MediaAsset) -> None:
        """MediaAsset доступен только в текущей беседе (или без привязки)."""
        if self._conversation_id is None:
            return
        if asset.conversation_id is None:
            return
        if asset.conversation_id != self._conversation_id:
            raise ValueError(f"Изображение {asset.id} принадлежит другой беседе")

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
            async with self._borrow_session() as session:
                service = AttachmentService(session)
                att = await service.get_by_id(attachment_id)
                if att is None:
                    raise ValueError(f"Вложение {attachment_id} не найдено")
                self._assert_attachment_scope(att)
                if not att.mime_type.startswith("image/"):
                    raise ValueError("img2img поддерживает только изображения")
                if att.media_asset_id is not None:
                    media = MediaService(session)
                    result = await media.get_bytes(
                        att.media_asset_id,
                        trusted_internal=True,
                    )
                    if result is None:
                        raise ValueError(
                            f"Изображение вложения {attachment_id} не найдено в БД",
                        )
                    data, _mime = result
                    return data, att.original_name
                path = AttachmentService.file_path(att)
                return path.read_bytes(), att.original_name

        if not url_or_path or not str(url_or_path).strip():
            raise ValueError("Укажите init_image_url или attachment_id")

        raw = str(url_or_path).strip()
        asset_id = parse_asset_id_from_url(raw)
        if asset_id is not None:
            async with self._borrow_session() as session:
                media = MediaService(session)
                asset = await media.get_asset(asset_id)
                if asset is not None:
                    self._assert_media_asset_scope(asset)
                result = await media.get_bytes(asset_id, trusted_internal=True)
                if result is not None:
                    data, _mime = result
                    return data, f"{asset_id}.png"
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
        if self._source_user_message_id is None:
            return None

        async with self._borrow_session() as session:
            att_repo = AttachmentRepository(session)
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

            msg = await MessageRepository(session).get_by_id(
                self._source_user_message_id,
            )
            if msg and msg.content_json and isinstance(
                msg.content_json.get("parts"),
                list,
            ):
                for part in msg.content_json["parts"]:
                    if part.get("type") != "image_url":
                        continue
                    url = _public_url_from_image_part(part)
                    raw_asset = part.get("asset_id")
                    try:
                        if raw_asset:
                            aid = uuid.UUID(str(raw_asset))
                            media = MediaService(session)
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
            if self._emit_image is not None and self._conversation_id is not None:
                return await self._run_img2img_streaming(args)
            return await self._run_sd_image_tool(img2img, args, "img2img")
        except ValueError as exc:
            logger.warning("img2img: %s", exc)
            return ToolResult(content=f"Ошибка img2img: {exc}", image_urls=[])
        except RuntimeError as exc:
            return ToolResult(content=str(exc), image_urls=[])

    async def _ingest_img2img_variant_item(
        self,
        item: dict[str, str | int | float],
    ) -> tuple[str | None, str | None, dict[str, str]]:
        """Перенести один файл generated/ в БД и вернуть canonical URL."""
        url = str(item.get("url") or "").strip()
        if not url:
            return None, None, {}
        snippet = f"Изображение:\n  URL: {url}\n"
        async with self._borrow_session() as session:
            media = MediaService(session)
            ingested, url_map, raw_ids = await media.ingest_sd_output_files(
                snippet,
                conversation_id=self._conversation_id,
            )
            if self._session is None:
                await session.commit()
        if not ingested:
            logger.warning("img2img variant ingest пустой для %s", url)
            return None, None, {}
        from app.services.message_builder import canonical_stored_image_urls

        aids = [str(i) for i in raw_ids]
        canonical = canonical_stored_image_urls(ingested, aids)
        out_url = canonical[0] if canonical else ingested[0]
        return out_url, aids[0] if aids else None, url_map

    async def _emit_ingested_img2img_variant(
        self,
        item: dict[str, str | int | float],
    ) -> tuple[str | None, str | None, dict[str, str]]:
        url, aid, url_map = await self._ingest_img2img_variant_item(item)
        if url and self._emit_image is not None:
            await self._emit_image([url], [aid] if aid else None)
        return url, aid, url_map

    @staticmethod
    def _cancel_img2img_variant_futures(
        futures: list[Future[tuple[str | None, str | None, dict[str, str]]]],
    ) -> None:
        for fut in futures:
            if not fut.done():
                fut.cancel()

    @staticmethod
    async def _await_img2img_variant_futures(
        futures: list[Future[tuple[str | None, str | None, dict[str, str]]]],
    ) -> tuple[list[str], list[str], dict[str, str]]:
        urls: list[str] = []
        aids: list[str] = []
        url_map: dict[str, str] = {}
        for fut in futures:
            try:
                url, aid, part_map = await asyncio.wrap_future(fut)
            except Exception as exc:
                logger.warning("img2img variant callback: %s", exc)
                continue
            if url and url not in urls:
                urls.append(url)
            if aid and aid not in aids:
                aids.append(aid)
            url_map.update(part_map)
        return urls, aids, url_map

    async def _run_img2img_streaming(self, arguments: dict[str, Any]) -> ToolResult:
        """img2img с callback: каждый denoise → ingest → WS image до конца инструмента."""
        loop = asyncio.get_running_loop()
        variant_futures: list[Future[tuple[str | None, str | None, dict[str, str]]]] = []

        def on_variant(item: dict[str, str | int | float]) -> None:
            variant_futures.append(
                asyncio.run_coroutine_threadsafe(
                    self._emit_ingested_img2img_variant(item),
                    loop,
                ),
            )

        sig = inspect.signature(img2img)
        filtered = {
            k: v for k, v in arguments.items() if k in sig.parameters and v is not None
        }
        if self._sd_webui_url is not None:
            filtered["sd_webui_url"] = self._sd_webui_url
        filtered["on_variant_saved"] = on_variant
        filtered["cancel_check"] = (
            lambda: self._cancel_event is not None and self._cancel_event.is_set()
        )

        t0 = time.monotonic()
        poll_stop = asyncio.Event()
        poll_task: asyncio.Task[None] | None = None
        if self._emit_progress is not None:
            poll_task = asyncio.create_task(
                self._poll_sd_progress("img2img", poll_stop),
            )
        try:
            text = await self._with_tool_timeout(
                "img2img",
                heavy_job_queue.run_sync(
                    lambda: img2img(**filtered),
                    cancel_event=self._cancel_event,
                    operation="img2img",
                ),
            )
        except TimeoutError:
            self._cancel_img2img_variant_futures(variant_futures)
            return self._tool_timeout_result("img2img")
        except JobCancelled:
            self._cancel_img2img_variant_futures(variant_futures)
            return ToolResult(content="Генерация отменена", image_urls=[])
        except ShutdownInProgress:
            self._cancel_img2img_variant_futures(variant_futures)
            return ToolResult(
                content="Сервер завершает работу — генерация прервана. Обновите страницу позже.",
                image_urls=[],
            )
        except SdUnavailableError as exc:
            self._cancel_img2img_variant_futures(variant_futures)
            return ToolResult(content=str(exc), image_urls=[])
        except RuntimeError as exc:
            self._cancel_img2img_variant_futures(variant_futures)
            return ToolResult(content=str(exc), image_urls=[])
        finally:
            poll_stop.set()
            if poll_task is not None:
                poll_task.cancel()
                try:
                    await poll_task
                except asyncio.CancelledError:
                    pass

        streamed_urls, streamed_aids, url_map = await self._await_img2img_variant_futures(
            variant_futures,
        )
        logger.info(
            "img2img streaming: SD %.1fs, вариантов в чате %d",
            time.monotonic() - t0,
            len(streamed_urls),
        )

        if not streamed_urls:
            return await self._finalize_sd_tool_text(text, "img2img", t0)

        if url_map:
            from app.services.message_builder import rewrite_media_urls_in_text

            text = rewrite_media_urls_in_text(text, url_map)
        from app.services.message_builder import canonical_stored_image_urls

        urls = canonical_stored_image_urls(streamed_urls, streamed_aids)
        return ToolResult(
            content=text,
            image_urls=urls,
            image_asset_ids=streamed_aids or None,
            url_rewrites=url_map or None,
            images_streamed=True,
        )

    async def _finalize_sd_tool_text(
        self,
        text: str,
        tool_name: str,
        t0: float,
    ) -> ToolResult:
        """Ingest всех URL из отчёта SD (batch после синхронного инструмента)."""
        if self._emit_progress is not None and self._session is not None:
            await self._emit_progress(
                build_progress(STAGE_SAVE_MEDIA, tool=tool_name),
            )
        urls = self._parse_urls(text)
        url_map: dict[str, str] = {}
        asset_ids: list[str] = []
        async with self._borrow_session() as session:
            media = MediaService(session)
            ingested, url_map, raw_ids = await media.ingest_sd_output_files(
                text,
                conversation_id=self._conversation_id,
            )
            if ingested:
                urls = ingested
                asset_ids = [str(i) for i in raw_ids]
            elif urls:
                urls = await media.normalize_image_urls(
                    urls,
                    conversation_id=self._conversation_id,
                )
                for u in urls:
                    aid = parse_asset_id_from_url(u)
                    if aid:
                        asset_ids.append(str(aid))
            if self._session is None:
                await session.commit()
        if url_map:
            from app.services.message_builder import rewrite_media_urls_in_text

            text = rewrite_media_urls_in_text(text, url_map)
        if asset_ids:
            from app.services.message_builder import canonical_stored_image_urls

            urls = canonical_stored_image_urls(urls, asset_ids)
        duration_ms = int((time.monotonic() - t0) * 1000)
        log_event(
            logger,
            "sd_done",
            f"{tool_name} done urls={len(urls)} assets={len(asset_ids)}",
            tool=tool_name,
            duration_ms=duration_ms,
        )
        return ToolResult(
            content=text,
            image_urls=urls,
            image_asset_ids=asset_ids or None,
            url_rewrites=url_map or None,
        )

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
        log_event(logger, "sd_start", f"{tool_name} start", tool=tool_name)
        poll_stop = asyncio.Event()
        poll_task: asyncio.Task[None] | None = None
        if is_sd_tool(tool_name) and self._emit_progress is not None:
            poll_task = asyncio.create_task(
                self._poll_sd_progress(tool_name, poll_stop),
            )
        try:
            text = await self._with_tool_timeout(
                tool_name,
                heavy_job_queue.run_sync(
                    lambda: func(**filtered),
                    cancel_event=self._cancel_event,
                    operation=tool_name,
                ),
            )
        except TimeoutError:
            return self._tool_timeout_result(tool_name)
        except JobCancelled:
            return ToolResult(
                content="Генерация отменена",
                image_urls=[],
            )
        except ShutdownInProgress:
            return ToolResult(
                content="Сервер завершает работу — генерация прервана. Обновите страницу позже.",
                image_urls=[],
            )
        except SdUnavailableError as exc:
            return ToolResult(content=str(exc), image_urls=[])
        except RuntimeError as exc:
            return ToolResult(content=str(exc), image_urls=[])
        except requests.HTTPError as exc:
            return ToolResult(
                content=f"Ошибка SD {tool_name}: {exc}. Попробуйте «Загрузить модели».",
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
        logger.info(
            "%s SD завершён за %.1fs",
            tool_name,
            time.monotonic() - t0,
        )
        return await self._finalize_sd_tool_text(text, tool_name, t0)

    async def _request_sd_interrupt(self, sd_webui_url: str | None) -> None:
        """Прервать текущую генерацию на SD WebUI (cooperative cancel)."""
        sd_base = resolve_sd_webui_url(sd_webui_url)
        try:
            from app.integrations.sd_tools import get_sd_session

            await asyncio.to_thread(sd_interrupt, get_sd_session(), sd_base)
        except Exception as exc:
            logger.warning("SD interrupt: %s", exc)

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
        last_preview_at = 0.0
        last_preview_step = -1
        preview_interval = settings.sd_preview_min_interval_sec
        while not stop.is_set():
            if self._cancel_event is not None and self._cancel_event.is_set():
                await self._request_sd_interrupt(sd_url)
                break
            try:
                snapshot = await heavy_job_queue.run_sync(
                    lambda: fetch_sd_progress(sd_url),
                    operation="sd_progress_poll",
                )
            except Exception:
                snapshot = None
            if snapshot and snapshot.get("active") and emit is not None:
                payload = progress_from_sd_snapshot(tool_name, snapshot)
                preview = payload.get("preview")
                if preview:
                    step = int(snapshot.get("sampling_step") or -1)
                    now = time.monotonic()
                    if (
                        step != last_preview_step
                        or now - last_preview_at >= preview_interval
                    ):
                        last_preview_step = step
                        last_preview_at = now
                    else:
                        del payload["preview"]
                await emit(payload)
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
                attachment = await service.get_by_id(attachment_id)
                if attachment is None:
                    raise ValueError(f"Вложение не найдено: {attachment_id}")
                self._assert_attachment_scope(attachment)
                return await service.extract_text(
                    attachment_id,
                    max_chars=max_chars,
                    cancel_event=self._cancel_event,
                )
            except ValueError as exc:
                return f"Ошибка extract_text: {exc}"

        async with db_session.async_session_factory() as session:
            service = AttachmentService(session)
            try:
                attachment = await service.get_by_id(attachment_id)
                if attachment is None:
                    raise ValueError(f"Вложение не найдено: {attachment_id}")
                self._assert_attachment_scope(attachment)
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
