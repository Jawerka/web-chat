"""
Инструменты Stable Diffusion для MCP и in-process ToolExecutor (этап 5).

generate_image — txt2img через Automatic1111 WebUI API.
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

import requests
from fastmcp import FastMCP
from PIL import Image as PILImage
from PIL import PngImagePlugin

from app.config import settings
from app.integrations.media_utils import (
    GENERATED_ROOT,
    generated_media_url,
    make_thumbnail,
    save_image_from_base64,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_session: requests.Session | None = None


def get_sd_session() -> requests.Session:
    """Общая HTTP-сессия к SD WebUI."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"Content-Type": "application/json"})
        if settings.sd_auth_user and settings.sd_auth_pass:
            _session.auth = (settings.sd_auth_user, settings.sd_auth_pass)
    return _session


def generate_image(
    prompt: str,
    negative_prompt: str = "",
    steps: int = 22,
    width: int = 1024,
    height: int = 1024,
    cfg_scale: float = 5.0,
    sampler_name: str = "Euler a",
    scheduler: str = "",
    seed: int = -1,
    restore_faces: bool = False,
    tiling: bool = False,
    description: str = "",
    count: int = 1,
) -> str:
    """
    Сгенерировать изображение через SD WebUI (txt2img).

    Returns:
        Текстовый отчёт с URL (PUBLIC_BASE_URL/media/generated/...).
    """
    logger.info(
        "generate_image: prompt=%r count=%d %dx%d steps=%d seed=%s",
        prompt[:80],
        max(1, min(10, int(count))),
        width,
        height,
        steps,
        seed,
    )
    t0 = time.monotonic()

    if not (1 <= steps <= 150):
        raise ValueError("steps должен быть от 1 до 150")

    def _clamp_dim(value: int, default: int) -> int:
        """Привести размер к диапазону SD WebUI (768–2048, кратно 8)."""
        v = value if value else default
        v = max(768, min(2048, int(v)))
        return (v // 8) * 8

    width = _clamp_dim(width, settings.sd_width)
    height = _clamp_dim(height, settings.sd_height)
    if not (1 <= cfg_scale <= 30):
        raise ValueError("cfg_scale должен быть от 1 до 30")

    current_seed = seed if seed != -1 else random.randint(0, 2**32 - 1)
    image_count = max(1, min(10, int(count)))

    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt or settings.sd_negative_prompt,
        "steps": steps,
        "width": width,
        "height": height,
        "cfg_scale": cfg_scale,
        "sampler_name": sampler_name or settings.sd_sampler,
        "scheduler": scheduler or settings.sd_schedule_type,
        "seed": current_seed,
        "n_iter": image_count,
        "batch_size": 1,
        "tiling": tiling,
        "restore_faces": restore_faces,
    }

    session = get_sd_session()
    url = f"{settings.sd_webui_url.rstrip('/')}/sdapi/v1/txt2img"
    try:
        resp = session.post(url, json=payload, timeout=settings.request_timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        elapsed = time.monotonic() - t0
        logger.error(
            "SD txt2img ошибка за %.1fs: %s (url=%s)",
            elapsed,
            exc,
            url,
        )
        raise
    data = resp.json()

    images_b64 = data.get("images", [])
    if not images_b64:
        logger.error(
            "SD txt2img: пустой images за %.1fs, keys=%s",
            time.monotonic() - t0,
            list(data.keys()),
        )
        return "Ошибка: WebUI не вернул изображений."
    logger.info(
        "SD txt2img OK: %d изображений за %.1fs",
        len(images_b64),
        time.monotonic() - t0,
    )

    png_info_text = ""
    try:
        info_resp = session.post(
            f"{settings.sd_webui_url.rstrip('/')}/sdapi/v1/png-info",
            json={"image": f"data:image/png;base64,{images_b64[0]}"},
            timeout=settings.request_timeout,
        )
        info_resp.raise_for_status()
        png_info_text = info_resp.json().get("info", "")
    except requests.RequestException as exc:
        logger.warning("png-info недоступен: %s", exc)

    results: list[dict[str, str | int]] = []
    for img_b64 in images_b64:
        filename = save_image_from_base64(img_b64)
        make_thumbnail(filename)

        if description or png_info_text:
            try:
                img_path = GENERATED_ROOT / filename
                with PILImage.open(img_path) as img:
                    meta = PngImagePlugin.PngInfo()
                    if png_info_text:
                        meta.add_text("parameters", png_info_text)
                    if description:
                        meta.add_text("Description", description)
                    img.save(img_path, pnginfo=meta)
            except OSError as exc:
                logger.warning("Метаданные PNG для %s: %s", filename, exc)

        results.append({
            "filename": filename,
            "url": generated_media_url(filename),
            "seed": current_seed,
        })

    lines = [
        f"Генерация завершена ({len(results)} изображений).",
        f"Prompt: {prompt}",
        "",
    ]
    for i, item in enumerate(results, 1):
        lines.append(f"Изображение {i} (seed {item['seed']}):")
        lines.append(f"  URL: {item['url']}")
        lines.append("")

    if png_info_text:
        lines.extend(["--- Параметры генерации ---", png_info_text])

    logger.info(
        "generate_image готово: %d файлов, total %.1fs",
        len(results),
        time.monotonic() - t0,
    )
    return "\n".join(lines)


def register_sd_tools(mcp: FastMCP) -> None:
    """Зарегистрировать MCP-инструменты SD на сервере FastMCP."""

    @mcp.tool()
    def generate_image_tool(
        prompt: str,
        negative_prompt: str = "",
        steps: int = 22,
        width: int = 1024,
        height: int = 1024,
        cfg_scale: float = 5.0,
        sampler_name: str = "Euler a",
        scheduler: str = "",
        seed: int = -1,
        restore_faces: bool = False,
        tiling: bool = False,
        description: str = "",
        count: int = 1,
    ) -> str:
        """
        Сгенерировать изображение через Stable Diffusion (txt2img).

        В ответе — HTTP URL вида {PUBLIC_BASE_URL}/media/generated/...
        """
        return generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=steps,
            width=width,
            height=height,
            cfg_scale=cfg_scale,
            sampler_name=sampler_name,
            scheduler=scheduler,
            seed=seed,
            restore_faces=restore_faces,
            tiling=tiling,
            description=description,
            count=count,
        )
