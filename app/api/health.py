"""
Эндпоинт проверки живости процесса.

На этапе 1 — статический ответ без проверки внешних зависимостей.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Проверка живости процесса (без внешних зависимостей)."""
    return {"status": "ok"}
