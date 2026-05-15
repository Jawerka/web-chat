"""
CLI для ручной проверки агента (этап 5).

Пример:
    python -m app.scripts.test_agent "Нарисуй закат над морем"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.db.seed import DEFAULT_PROMPT, IMAGE_GEN_PROMPT
from app.integrations.llm_client import LLMError
from app.services.agent_orchestrator import AgentOrchestrator, ToolLoopExceeded

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


async def _emit_event(event_type: str, payload: dict) -> None:
    """Печать событий агента в stdout."""
    if event_type == "tool_start":
        print(f"\n[tool_start] {payload.get('name')} …")
    elif event_type == "tool_done":
        print(f"[tool_done] {payload.get('name')}")
    elif event_type == "image":
        for url in payload.get("urls", []):
            print(f"[image] {url}")
    elif event_type == "text_delta":
        print(payload.get("content", ""), end="", flush=True)


async def main() -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="Тест агента web-chat")
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Привет! Кратко ответь, кто ты.",
        help="Сообщение пользователя",
    )
    parser.add_argument(
        "--preset",
        choices=["default", "image_gen"],
        default="image_gen",
        help="Системный промпт (image_gen для генерации картинок)",
    )
    args = parser.parse_args()

    if args.preset == "image_gen":
        system = IMAGE_GEN_PROMPT
    elif args.preset == "default":
        system = DEFAULT_PROMPT
    else:
        system = None
    orchestrator = AgentOrchestrator()

    print(f"Запрос: {args.prompt}\n")
    try:
        result = await orchestrator.run_turn(
            args.prompt,
            system_prompt=system,
            emit=_emit_event,
        )
    except ToolLoopExceeded as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"\nОшибка LLM: {exc}", file=sys.stderr)
        return 1

    if result.assistant_text and not result.assistant_text.endswith("\n"):
        print()
    if result.image_urls:
        print("\n--- URL изображений ---")
        for url in result.image_urls:
            print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
