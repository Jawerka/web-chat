"""
Состояние вызовов tools в одном ходе диалога (P1.4).

Детект дубликатов (name + args) и лимит повторов тяжёлых SD-tools.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.integrations.media_utils import parse_asset_id_from_url

SD_TOOL_NAMES = frozenset({"generate_image", "img2img", "upscale_images"})


def _normalize_tool_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Свести эквивалентные аргументы (img2img: asset URL vs attachment_id)."""
    if name != "img2img":
        return args
    out = {k: v for k, v in args.items() if k not in ("init_image_url", "attachment_id")}
    init_ref: str | None = None
    raw_att = args.get("attachment_id")
    if raw_att:
        init_ref = str(raw_att).strip()
    else:
        init_url = args.get("init_image_url")
        if init_url:
            aid = parse_asset_id_from_url(str(init_url))
            if aid is not None:
                init_ref = str(aid)
    if init_ref:
        out["_init_ref"] = init_ref
    return out


def tool_call_signature(name: str, args: dict[str, Any]) -> str:
    """Стабильный ключ вызова для сравнения в рамках одного turn."""
    normalized = _normalize_tool_args(name, args)
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    return f"{name}:{payload}"


class ConversationToolState:
    """Учёт tool-вызовов в одном turn; выбрасывает TurnCancelled / ToolAntiLoopExceeded."""

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
        from app.services.agent_orchestrator import ToolAntiLoopExceeded, TurnCancelled

        if cancel_event.is_set():
            raise TurnCancelled("Генерация отменена")

        sig = tool_call_signature(name, args)
        if sig in self._signatures:
            raise ToolAntiLoopExceeded(
                f"Повторный вызов {name} с теми же аргументами в одном ходе",
                kind="duplicate",
            )
        self._signatures.add(sig)

        self._counts_by_name[name] = self._counts_by_name.get(name, 0) + 1
        count = self._counts_by_name[name]
        if name in SD_TOOL_NAMES and count > self._max_same:
            raise ToolAntiLoopExceeded(
                f"Слишком много вызовов {name} в одном ходе "
                f"(максимум {self._max_same})",
                kind="max_same",
            )
