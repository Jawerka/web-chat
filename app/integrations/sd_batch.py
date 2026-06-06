"""
batch_size и n_iter для запросов Stable Diffusion WebUI.

batch_size всегда 1 (одно изображение за проход сэмплера).
Число вариантов задаётся через n_iter (в txt2img — параметр count инструмента).
"""

from __future__ import annotations

from app.config import settings

# Закреплено: не увеличивать batch_size без отдельного решения по VRAM/таймаутам.
SD_BATCH_SIZE = 1


def clamp_txt2img_n_iter(count: int) -> int:
    """Привести count к допустимому n_iter (1 … sd_txt2img_max_n_iter)."""
    cap = settings.sd_txt2img_max_n_iter
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(cap, n))
