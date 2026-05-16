"""
Инструменты Stable Diffusion для MCP и in-process ToolExecutor (этап 5).

generate_image — txt2img через Automatic1111 WebUI API.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING

import requests
from fastmcp import FastMCP
from PIL import Image as PILImage
from PIL import PngImagePlugin

from app.config import settings
from app.integrations.media_utils import (
    GENERATED_ROOT,
    generate_filename,
    generated_media_url,
    generated_thumb_url,
    make_thumbnail,
    resolve_trusted_generated_source,
    save_image_from_base64,
)
from app.integrations.runtime_config import resolve_sd_webui_url

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
    sd_webui_url: str | None = None,
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

    sd_base = resolve_sd_webui_url(sd_webui_url)
    session = get_sd_session()
    url = f"{sd_base}/sdapi/v1/txt2img"
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
            f"{sd_base}/sdapi/v1/png-info",
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

        results.append(
            {
                "filename": filename,
                "url": generated_media_url(filename),
                "seed": current_seed,
            }
        )

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


def validate_upscaler(name: str, sd_webui_url: str | None = None) -> None:
    """Проверить, что upscaler доступен в SD WebUI."""
    sd_base = resolve_sd_webui_url(sd_webui_url)
    session = get_sd_session()
    resp = session.get(f"{sd_base}/sdapi/v1/upscalers", timeout=settings.request_timeout)
    resp.raise_for_status()
    available = [u["name"] for u in resp.json()]
    if name not in available:
        raise ValueError(f"Upscaler '{name}' не найден. Доступны: {available}")


def img2img(
    prompt: str,
    init_image_url: str = "",
    *,
    init_image_bytes: bytes | None = None,
    init_source_name: str = "",
    negative_prompt: str = "",
    steps: int = 22,
    width: int = 1024,
    height: int = 1024,
    cfg_scale: float = 5.0,
    sampler_name: str = "Euler a",
    scheduler: str = "",
    seed: int = -1,
    denoising_strength: float = 0.52,
    restore_faces: bool = False,
    tiling: bool = False,
    resize_mode: int = 0,
    description: str = "",
    sd_webui_url: str | None = None,
) -> str:
    """
    img2img: доработка существующего изображения через SD WebUI.

    init_image_url — URL или имя файла (если bytes не переданы из ToolExecutor).
    """
    if not prompt.strip():
        raise ValueError("prompt не может быть пустым")

    if init_image_bytes is None:
        if not init_image_url.strip():
            raise ValueError("Укажите init_image_url или передайте init_image_bytes")
        init_path = resolve_trusted_generated_source(init_image_url)
        init_image_bytes = init_path.read_bytes()
        init_source_name = init_source_name or init_path.name

    if not (1 <= steps <= 150):
        raise ValueError("steps должен быть от 1 до 150")
    if not (512 <= width <= 2048):
        raise ValueError("width должен быть от 512 до 2048")
    if width % 8 != 0:
        raise ValueError("width должен быть кратен 8")
    if not (512 <= height <= 2048):
        raise ValueError("height должен быть от 512 до 2048")
    if height % 8 != 0:
        raise ValueError("height должен быть кратен 8")
    if not (1 <= cfg_scale <= 30):
        raise ValueError("cfg_scale должен быть от 1 до 30")
    if not (0.0 <= denoising_strength <= 1.0):
        raise ValueError("denoising_strength должен быть от 0.0 до 1.0")
    if resize_mode not in (0, 1, 2, 3):
        raise ValueError("resize_mode должен быть от 0 до 3")

    current_seed = seed if seed != -1 else random.randint(0, 2**32 - 1)
    init_b64 = base64.b64encode(init_image_bytes).decode("utf-8")

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
        "n_iter": 1,
        "tiling": tiling,
        "restore_faces": restore_faces,
        "init_images": [init_b64],
        "resize_mode": resize_mode,
        "denoising_strength": denoising_strength,
        "send_images": True,
        "save_images": False,
    }

    sd_base = resolve_sd_webui_url(sd_webui_url)
    session = get_sd_session()
    t0 = time.monotonic()
    try:
        resp = session.post(
            f"{sd_base}/sdapi/v1/img2img",
            json=payload,
            timeout=settings.request_timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("SD img2img ошибка: %s", exc)
        raise

    data = resp.json()
    images_b64 = data.get("images", [])
    if not images_b64:
        return "Ошибка: WebUI не вернул изображений."

    parameters = data.get("parameters", {})
    info_text = data.get("info", "")
    if isinstance(parameters, str):
        png_params = parameters
    else:
        png_params = json.dumps(parameters, ensure_ascii=False, indent=2)

    results: list[dict[str, str | int]] = []
    for img_b64 in images_b64:
        filename = save_image_from_base64(img_b64)
        thumb_name = make_thumbnail(filename)

        try:
            img_path = GENERATED_ROOT / filename
            with PILImage.open(img_path) as img:
                meta = PngImagePlugin.PngInfo()
                if png_params:
                    meta.add_text("parameters", png_params)
                if info_text:
                    meta.add_text("info", str(info_text))
                if description:
                    meta.add_text("Description", description)
                if init_source_name:
                    meta.add_text("Init image", init_source_name)
                img.save(img_path, pnginfo=meta)
        except OSError as exc:
            logger.warning("Метаданные PNG для %s: %s", filename, exc)

        item: dict[str, str | int] = {
            "filename": filename,
            "url": generated_media_url(filename),
            "seed": current_seed,
        }
        if thumb_name:
            item["thumb_url"] = generated_thumb_url(thumb_name)
        results.append(item)

    lines = [
        f"img2img завершён ({len(results)} изображений).",
        f"Prompt: {prompt}",
        f"Исходник: {init_source_name}",
        f"Denoising strength: {denoising_strength}",
        "",
    ]
    for i, item in enumerate(results, 1):
        lines.append(f"Изображение {i} (seed {item['seed']}):")
        lines.append(f"  URL: {item['url']}")
        if item.get("thumb_url"):
            lines.append(f"  Thumbnail: {item['thumb_url']}")
        lines.append("")

    lines.extend(["--- Параметры генерации ---", str(info_text or png_params)])
    logger.info("img2img готово за %.1fs", time.monotonic() - t0)
    return "\n".join(lines)


def upscale_images(
    file_urls: list[str],
    resize_mode: int = 0,
    upscaling_resize: int = 2,
    upscaling_resize_w: int = 512,
    upscaling_resize_h: int = 512,
    upscaler_1: str = "R-ESRGAN 4x+",
    upscaler_2: str = "None",
    sd_webui_url: str | None = None,
) -> str:
    """Апскейл изображений через /sdapi/v1/extra-single-image."""
    if not file_urls:
        return "Ошибка: не указаны файлы для апскейла."

    try:
        validate_upscaler(upscaler_1, sd_webui_url=sd_webui_url)
    except (ValueError, requests.RequestException) as exc:
        return f"Ошибка: {exc}"

    sd_base = resolve_sd_webui_url(sd_webui_url)
    session = get_sd_session()
    results: list[dict[str, str]] = []

    for url_or_path in file_urls:
        try:
            file_path = resolve_trusted_generated_source(url_or_path)
        except (ValueError, FileNotFoundError) as exc:
            return f"Ошибка: {exc}"

        original_name = file_path.name
        img_data = file_path.read_bytes()
        b64_image = base64.b64encode(img_data).decode("utf-8")

        minimal_payload = {
            "resize_mode": resize_mode,
            "upscaling_resize": upscaling_resize,
            "upscaler_1": upscaler_1,
            "image": b64_image,
        }
        full_payload = {
            **minimal_payload,
            "show_extras_results": True,
            "gfpgan_visibility": 0,
            "codeformer_visibility": 0,
            "codeformer_weight": 0,
            "upscaling_resize_w": upscaling_resize_w,
            "upscaling_resize_h": upscaling_resize_h,
            "upscaling_crop": True,
            "upscaler_2": upscaler_2,
            "extras_upscaler_2_visibility": 0,
            "upscale_first": False,
        }
        payloads = [minimal_payload]
        if upscaler_2 != "None" or resize_mode != 0:
            payloads.append(full_payload)

        upscaled_b64 = None
        last_error = None
        for payload in payloads:
            try:
                resp = session.post(
                    f"{sd_base}/sdapi/v1/extra-single-image",
                    json=payload,
                    timeout=settings.request_timeout,
                )
                if not resp.ok:
                    last_error = resp.text
                    continue
                data = resp.json()
                upscaled_b64 = data.get("image")
                if upscaled_b64:
                    break
                last_error = "WebUI не вернул изображение"
            except requests.RequestException as exc:
                last_error = str(exc)

        if not upscaled_b64:
            return f"Ошибка апскейла для {url_or_path}: {last_error}"

        filename = save_image_from_base64(
            upscaled_b64,
            generate_filename(prefix=f"upscaled_{Path(original_name).stem}"),
        )
        thumb_name = make_thumbnail(filename)
        entry: dict[str, str] = {
            "filename": filename,
            "url": generated_media_url(filename),
        }
        if thumb_name:
            entry["thumb_url"] = generated_thumb_url(thumb_name)
        results.append(entry)

    lines = [f"Апскейл завершён ({len(results)} изображений).", ""]
    for i, item in enumerate(results, 1):
        lines.append(f"Изображение {i}:")
        lines.append(f"  URL: {item['url']}")
        if item.get("thumb_url"):
            lines.append(f"  Thumbnail: {item['thumb_url']}")
        lines.append("")
    return "\n".join(lines)


def get_gallery(limit: int = 20) -> str:
    """Список изображений: MediaAsset в БД + локальные файлы в data/generated/."""
    import asyncio
    import concurrent.futures

    from app.db.session import async_session_factory
    from app.services.gallery_service import list_gallery_images

    cap = max(1, min(100, int(limit)))

    async def _fetch() -> list:
        async with async_session_factory() as session:
            return await list_gallery_images(session, limit=cap)

    def _run_sync() -> list:
        return asyncio.run(_fetch())

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            items = pool.submit(_run_sync).result(timeout=120)
    except RuntimeError:
        items = _run_sync()

    if not items:
        return "Галерея пуста."

    lines = [f"Галерея ({len(items)} изображений):", ""]
    for item in items:
        src = "БД" if item.source == "db" else "диск"
        lines.append(f"  - {item.filename} ({item.size_kb} KB, {src})")
        lines.append(f"    URL: {item.url}")
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

    @mcp.tool()
    def img2img_tool(
        prompt: str,
        init_image_url: str,
        negative_prompt: str = "",
        steps: int = 22,
        width: int = 1024,
        height: int = 1024,
        cfg_scale: float = 5.0,
        sampler_name: str = "Euler a",
        scheduler: str = "",
        seed: int = -1,
        denoising_strength: float = 0.52,
        restore_faces: bool = False,
        tiling: bool = False,
        resize_mode: int = 0,
        description: str = "",
    ) -> str:
        """Доработать изображение (img2img). init_image_url — URL из чата или имя файла."""
        return img2img(
            prompt=prompt,
            init_image_url=init_image_url,
            negative_prompt=negative_prompt,
            steps=steps,
            width=width,
            height=height,
            cfg_scale=cfg_scale,
            sampler_name=sampler_name,
            scheduler=scheduler,
            seed=seed,
            denoising_strength=denoising_strength,
            restore_faces=restore_faces,
            tiling=tiling,
            resize_mode=resize_mode,
            description=description,
        )

    @mcp.tool()
    def upscale_images_tool(
        file_urls: list[str],
        resize_mode: int = 0,
        upscaling_resize: int = 2,
        upscaling_resize_w: int = 512,
        upscaling_resize_h: int = 512,
        upscaler_1: str = "R-ESRGAN 4x+",
        upscaler_2: str = "None",
    ) -> str:
        """Увеличить разрешение изображений (только локальные /media/generated/…)."""
        return upscale_images(
            file_urls=file_urls,
            resize_mode=resize_mode,
            upscaling_resize=upscaling_resize,
            upscaling_resize_w=upscaling_resize_w,
            upscaling_resize_h=upscaling_resize_h,
            upscaler_1=upscaler_1,
            upscaler_2=upscaler_2,
        )

    @mcp.tool()
    def get_gallery_tool(limit: int = 20) -> str:
        """Список последних сгенерированных изображений."""
        return get_gallery(limit=limit)
