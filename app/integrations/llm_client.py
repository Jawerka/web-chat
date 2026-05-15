"""
Клиент LLM (OpenAI-compatible async API).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from app.config import settings
from app.integrations.tool_definitions import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Ошибка при обращении к LLM."""


@dataclass
class LLMCompletion:
    """Нормализованный ответ одного шага LLM."""

    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None


class LLMClient:
    """Async OpenAI-клиент к локальному LLM."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url.rstrip("/"),
            api_key=settings.llm_api_key or "not-needed",
            timeout=settings.llm_timeout_sec,
        )
        self._model: str | None = settings.llm_model or None

    async def resolve_model(self) -> str:
        """Вернуть имя модели: из настроек или первую из GET /v1/models."""
        if self._model:
            return self._model
        try:
            models = await self._client.models.list()
            if not models.data:
                raise LLMError("Список моделей LLM пуст")
            self._model = models.data[0].id
            logger.info("Автовыбор модели LLM: %s", self._model)
            return self._model
        except Exception as exc:
            raise LLMError(f"Не удалось получить список моделей: {exc}") from exc

    @staticmethod
    def _parse_completion(response: ChatCompletion) -> LLMCompletion:
        """Преобразовать ответ API в LLMCompletion."""
        choice = response.choices[0]
        message = choice.message
        tool_calls: list[dict[str, Any]] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })
        return LLMCompletion(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
    ) -> LLMCompletion:
        """
        Один запрос chat.completions без стриминга.

        Args:
            messages: История в формате OpenAI.
            tools: Определения инструментов; по умолчанию TOOL_DEFINITIONS.

        Raises:
            LLMError: Ошибка API или сети.
        """
        model = await self.resolve_model()
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools is not None else TOOL_DEFINITIONS,
                tool_choice="auto",
            )
        except Exception as exc:
            raise LLMError(f"Ошибка LLM: {exc}") from exc
        return self._parse_completion(response)

    async def complete_with_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
        cancel_event: asyncio.Event | None = None,
        tools: list[dict] | None = None,
    ) -> LLMCompletion:
        """
        Стриминг с накоплением content и tool_calls.

        Вызывает on_text_delta для каждого фрагмента текста.
        """
        model = await self.resolve_model()
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools is not None else TOOL_DEFINITIONS,
                tool_choice="auto",
                stream=True,
            )
        except Exception as exc:
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
    ) -> AsyncIterator[ChatCompletionChunk]:
        """
        Стриминг chat.completions (для WebSocket на этапе 7).

        Yields:
            Чанки ответа OpenAI.
        """
        model = await self.resolve_model()
        try:
            stream = await self._client.chat.completions.create(
                model=model,
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
