"""
Клиент LLM (OpenAI-compatible async API).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from app.config import settings
from app.diag_logging import log_event, redact_url
from app.integrations.runtime_config import resolve_llm_base_url
from app.integrations.tool_definitions import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

# Кэш автовыбора модели: base_url -> (model_id, monotonic_expires)
_MODEL_CACHE: dict[str, tuple[str, float]] = {}
_MODEL_CACHE_LOCK = asyncio.Lock()


class LLMError(Exception):
    """Ошибка при обращении к LLM."""


@dataclass
class LLMCompletion:
    """Нормализованный ответ одного шага LLM."""

    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None


def _is_model_loading_error(exc: BaseException) -> bool:
    if isinstance(exc, APIStatusError) and exc.status_code == 503:
        return True
    text = str(exc).lower()
    return "503" in text and "loading" in text


class LLMClient:
    """Async OpenAI-клиент к локальному LLM."""

    def __init__(self, *, base_url: str | None = None) -> None:
        self._base_url = resolve_llm_base_url(base_url)
        self._client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=settings.llm_api_key or "not-needed",
            timeout=settings.llm_timeout_sec,
            max_retries=0,
        )
        self._model: str | None = settings.llm_model or None

    async def resolve_model(self, override: str | None = None) -> str:
        """
        Вернуть имя модели: override, из настроек или первая из GET /v1/models.

        При 503 «Loading model» — повтор с паузой (до llm_model_load_wait_sec).
        """
        if override and override.strip():
            return override.strip()
        if self._model:
            return self._model

        cached = _MODEL_CACHE.get(self._base_url)
        if cached and cached[1] > time.monotonic():
            self._model = cached[0]
            return self._model

        model_id = await self._fetch_first_model_id()
        self._model = model_id
        async with _MODEL_CACHE_LOCK:
            _MODEL_CACHE[self._base_url] = (model_id, time.monotonic() + 300.0)
        logger.info("Автовыбор модели LLM: %s (base=%s)", model_id, self._base_url)
        return model_id

    async def _fetch_first_model_id(self) -> str:
        url = f"{self._base_url.rstrip('/')}/models"
        headers: dict[str, str] = {}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"

        deadline = time.monotonic() + max(5, settings.llm_model_load_wait_sec)
        attempt = 0
        last_exc: Exception | None = None

        while time.monotonic() < deadline:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, headers=headers or None)
                if response.is_success:
                    data = response.json()
                    models = data.get("data") if isinstance(data, dict) else None
                    if not models:
                        raise LLMError("Список моделей LLM пуст")
                    first = models[0]
                    model_id = first.get("id") if isinstance(first, dict) else str(first)
                    if not model_id:
                        raise LLMError("Список моделей LLM пуст")
                    if attempt > 1:
                        logger.info(
                            "LLM models: готово с попытки %d (HTTP %s)",
                            attempt,
                            response.status_code,
                        )
                    return str(model_id)

                body_preview = (response.text or "")[:200]
                if response.status_code == 503:
                    logger.warning(
                        "LLM models: HTTP 503 (попытка %d), тело=%r — ожидание загрузки модели",
                        attempt,
                        body_preview,
                    )
                    last_exc = LLMError(
                        f"Не удалось получить список моделей: Error code: 503 - {body_preview}",
                    )
                else:
                    logger.warning(
                        "LLM models: HTTP %s (попытка %d), тело=%r",
                        response.status_code,
                        attempt,
                        body_preview,
                    )
                    last_exc = LLMError(
                        f"Не удалось получить список моделей: HTTP {response.status_code}",
                    )
            except httpx.HTTPError as exc:
                logger.warning("LLM models: сеть (попытка %d): %s", attempt, exc)
                last_exc = LLMError(f"Не удалось получить список моделей: {exc}")

            await asyncio.sleep(settings.llm_model_load_retry_sec)

        if last_exc is not None:
            raise last_exc
        raise LLMError("Не удалось получить список моделей: таймаут ожидания загрузки LLM")

    @staticmethod
    def _parse_completion(response: ChatCompletion) -> LLMCompletion:
        """Преобразовать ответ API в LLMCompletion."""
        choice = response.choices[0]
        message = choice.message
        tool_calls: list[dict[str, Any]] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
        return LLMCompletion(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
        )

    async def complete_plain_text(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 64,
        temperature: float = 0.3,
        disable_thinking: bool = False,
        allow_reasoning_fallback: bool = True,
    ) -> str:
        """
        Короткий текстовый ответ без tools (заголовки, метки и т.п.).

        disable_thinking — для Qwen/vLLM: chat_template_kwargs.enable_thinking=false.

        Raises:
            LLMError: Ошибка API или сети.
        """
        model_name = await self.resolve_model(model)
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if disable_thinking:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False},
            }
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(f"Ошибка LLM: {exc}") from exc
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        if content:
            return content
        if not allow_reasoning_fallback:
            return ""
        reasoning = getattr(choice.message, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning.strip():
            logger.debug(
                "complete_plain_text: пустой content, finish_reason=%s — пробуем reasoning_content",
                choice.finish_reason,
            )
            for line in reversed(reasoning.strip().splitlines()):
                line = line.strip().strip("\"'«»“”")
                if line and len(line) <= 200:
                    return line
        return ""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> LLMCompletion:
        """
        Один запрос chat.completions без стриминга.

        Args:
            messages: История в формате OpenAI.
            tools: Определения инструментов; по умолчанию TOOL_DEFINITIONS.

        Raises:
            LLMError: Ошибка API или сети.
        """
        model_name = await self.resolve_model(model)
        try:
            response = await self._client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools if tools is not None else TOOL_DEFINITIONS,
                tool_choice="auto",
            )
        except Exception as exc:
            if _is_model_loading_error(exc):
                logger.warning("LLM chat: модель загружается (503), повтор resolve_model")
                self._model = None
                _MODEL_CACHE.pop(self._base_url, None)
                model_name = await self.resolve_model(model)
                try:
                    response = await self._client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        tools=tools if tools is not None else TOOL_DEFINITIONS,
                        tool_choice="auto",
                    )
                except Exception as retry_exc:
                    raise LLMError(f"Ошибка LLM: {retry_exc}") from retry_exc
            else:
                raise LLMError(f"Ошибка LLM: {exc}") from exc
        return self._parse_completion(response)

    async def complete_with_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
        cancel_event: asyncio.Event | None = None,
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> LLMCompletion:
        """
        Стриминг с накоплением content и tool_calls.

        Вызывает on_text_delta для каждого фрагмента текста.
        """
        model_name = await self.resolve_model(model)
        try:
            stream = await self._client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools if tools is not None else TOOL_DEFINITIONS,
                tool_choice="auto",
                stream=True,
            )
        except Exception as exc:
            log_event(
                logger,
                "llm_error",
                "LLM stream request failed",
                level=logging.ERROR,
                model=model_name,
                base_url=redact_url(self._base_url),
                error=str(exc)[:500],
            )
            raise LLMError(f"Ошибка стриминга LLM: {exc}") from exc

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        async for chunk in stream:
            if cancel_event is not None and cancel_event.is_set():
                break
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            if delta.content:
                content_parts.append(delta.content)
                if on_text_delta is not None:
                    await on_text_delta(delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if tc.index is not None else 0
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    acc = tool_calls_acc[idx]
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            acc["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            acc["function"]["arguments"] += tc.function.arguments

        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        return LLMCompletion(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """
        Стриминг chat.completions (для WebSocket на этапе 7).

        Yields:
            Чанки ответа OpenAI.
        """
        model_name = await self.resolve_model(model)
        try:
            stream = await self._client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools if tools is not None else TOOL_DEFINITIONS,
                tool_choice="auto",
                stream=True,
            )
            async for chunk in stream:
                yield chunk
        except Exception as exc:
            raise LLMError(f"Ошибка стриминга LLM: {exc}") from exc

    @staticmethod
    def parse_tool_arguments(arguments: str) -> dict[str, Any]:
        """Разобрать JSON аргументов tool call."""
        try:
            return json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            raise LLMError(f"Некорректные аргументы tool: {arguments}") from exc
