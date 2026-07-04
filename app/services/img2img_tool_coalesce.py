"""
Объединение нескольких img2img tool_calls в один пакет с denoising_strengths.

LLM часто шлёт N параллельных img2img с разным denoise вместо одного batch-вызова.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.integrations.img2img_service import MAX_IMG2IMG_VARIANTS
from app.integrations.media_utils import parse_asset_id_from_url

logger = logging.getLogger(__name__)

_DENOISE_KEYS = frozenset({"denoising_strength", "denoising_strengths"})
_MERGE_KEYS = frozenset(
    {
        "prompt",
        "negative_prompt",
        "width",
        "height",
        "steps",
        "cfg_scale",
        "sampler_name",
        "scheduler",
        "seed",
        "resize_mode",
        "restore_faces",
        "tiling",
        "description",
        "init_image_url",
        "attachment_id",
    }
)


def _init_ref(args: dict[str, Any]) -> str | None:
    raw_att = args.get("attachment_id")
    if raw_att:
        return str(raw_att).strip()
    init_url = args.get("init_image_url")
    if init_url:
        aid = parse_asset_id_from_url(str(init_url))
        if aid is not None:
            return str(aid)
        return str(init_url).strip()
    return None


def _merge_key(name: str, args: dict[str, Any]) -> tuple[Any, ...] | None:
    if name != "img2img":
        return None
    if not str(args.get("prompt") or "").strip():
        return None
    init = _init_ref(args)
    if not init:
        return None
    parts: list[Any] = [init, str(args.get("prompt") or "").strip()]
    for key in sorted(_MERGE_KEYS - {"prompt", "init_image_url", "attachment_id"}):
        val = args.get(key)
        if val is not None and val != "" and val != []:
            parts.append((key, val))
    return tuple(parts)


def extract_denoising_values(args: dict[str, Any]) -> list[float]:
    """Все denoise из аргументов одного вызова img2img."""
    raw_list = args.get("denoising_strengths")
    if isinstance(raw_list, list) and raw_list:
        return [float(v) for v in raw_list]
    if "denoising_strength" in args and args["denoising_strength"] is not None:
        return [float(args["denoising_strength"])]
    return []


def merge_img2img_args(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Собрать один набор аргументов img2img из нескольких совместимых."""
    base = dict(entries[0])
    strengths: list[float] = []
    seen: set[float] = set()
    for entry in entries:
        for value in extract_denoising_values(entry):
            rounded = round(float(value), 4)
            if rounded not in seen:
                seen.add(rounded)
                strengths.append(rounded)
    if not strengths:
        strengths = [0.54]
    if len(strengths) > MAX_IMG2IMG_VARIANTS:
        logger.warning(
            "img2img coalesce: %d denoise → обрезка до %d",
            len(strengths),
            MAX_IMG2IMG_VARIANTS,
        )
        strengths = strengths[:MAX_IMG2IMG_VARIANTS]
    merged = {k: v for k, v in base.items() if k not in _DENOISE_KEYS}
    merged["denoising_strengths"] = strengths
    return merged


def can_merge_img2img(name: str, args: dict[str, Any]) -> bool:
    return _merge_key(name, args) is not None


@dataclass
class ToolCallBatch:
    """Один или несколько tool_calls, исполняемых как единая операция."""

    entries: list[tuple[dict[str, Any], str, dict[str, Any]]] = field(default_factory=list)

    @property
    def primary(self) -> tuple[dict[str, Any], str, dict[str, Any]]:
        return self.entries[0]

    @property
    def name(self) -> str:
        return self.entries[0][1]

    def execution_args(self) -> dict[str, Any]:
        if len(self.entries) == 1:
            return dict(self.entries[0][2])
        if self.name == "img2img":
            return merge_img2img_args([args for _, _, args in self.entries])
        return dict(self.entries[0][2])

    @property
    def coalesced(self) -> bool:
        return len(self.entries) > 1


def group_tool_call_batches(
    parsed: list[tuple[dict[str, Any], str, dict[str, Any]]],
) -> list[ToolCallBatch]:
    """
    Сгруппировать tool_calls: совместимые img2img в одном completion → один batch.

    Порядок сохраняется: batch появляется на месте первого вызова группы.
    """
    if not parsed:
        return []

    groups_by_key: dict[tuple[Any, ...], list[int]] = {}
    index_key: list[tuple[Any, ...] | None] = []
    for i, (_, name, args) in enumerate(parsed):
        key = _merge_key(name, args)
        index_key.append(key)
        if key is not None:
            groups_by_key.setdefault(key, []).append(i)

    consumed: set[int] = set()
    batches: list[ToolCallBatch] = []

    for i, item in enumerate(parsed):
        if i in consumed:
            continue
        key = index_key[i]
        group_indices = groups_by_key.get(key, []) if key is not None else []
        if key is not None and len(group_indices) > 1:
            entries = [parsed[j] for j in group_indices]
            for j in group_indices:
                consumed.add(j)
            logger.info(
                "img2img coalesce: %d вызовов → 1 batch (denoising_strengths)",
                len(entries),
            )
            batches.append(ToolCallBatch(entries=entries))
        else:
            batches.append(ToolCallBatch(entries=[item]))
    return batches


_COALESCED_TOOL_NOTE = (
    "Объединено с другими вызовами img2img в этом шаге; результат — в ответе на первый вызов."
)

COALESCED_TOOL_NOTE = _COALESCED_TOOL_NOTE
