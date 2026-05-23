"""
Состояние вызовов tools в одном ходе диалога (P1.4).

Детект дубликатов (name + args) и лимит повторов тяжёлых SD-tools.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import settings

SD_TOOL_NAMES = frozenset({"generate_image", "img2img", "upscale_images"})


def tool_call_signature(name: str, args: dict[str, Any]) -> str:
    """Стабильный ключ вызова для сравнения в рамках одного turn."""
    payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return f"{name}:{payload}"


class ConversationToolState:
    """Учёт tool-вызовов в одном turn; выбрасывает TurnCancelled / ToolLoopExceeded."""

    def __init__(self, *, max_same_tool_per_turn: int | None = None) -> None:
        self._max_same = max_same_tool_per_turn or settings.max_same_tool_per_turn
        self._signatures: set[str] = set()
        self._counts_by_name: dict[str, int] = {}

    def before_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        cancel_event,
    ) -> None:
        """Проверить cancel и лимиты перед запуском tool."""
        from app.services.agent_orchestrator import ToolLoopExceeded, TurnCancelled

        if cancel_event.is_set():
            raise TurnCancelled("Генерация отменена")

        sig = tool_call_signature(name, args)
        if sig in self._signatures:
            raise ToolLoopExceeded(
                f"Повторный вызов {name} с теми же аргументами в одном ходе",
            )
        self._signatures.add(sig)

        self._counts_by_name[name] = self._counts_by_name.get(name, 0) + 1
        count = self._counts_by_name[name]
        if name in SD_TOOL_NAMES and count > self._max_same:
            raise ToolLoopExceeded(
                f"Слишком много вызовов {name} в одном ходе "
                f"(максимум {self._max_same})",
            )
