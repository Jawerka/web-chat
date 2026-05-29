"""Исключения хода агента (P3.8) — без зависимости от orchestrator."""

from __future__ import annotations


class ToolLoopExceeded(Exception):
    """Превышен лимит MAX_TOOL_ROUNDS."""


class ToolAntiLoopExceeded(ToolLoopExceeded):
    """P1.4: дубликат вызова или лимит одного SD-tool в ходе (без UI-ошибки)."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind  # "duplicate" | "max_same"


class TurnCancelled(Exception):
    """Генерация отменена пользователем."""
