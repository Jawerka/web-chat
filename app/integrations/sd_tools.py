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
from app.integrations.img2img_service import (
    Img2ImgRequest,
    PreparedInitImage,
    build_img2img_payload,
    encode_init_image_b64,
    format_generation_meta,
    normalize_denoising_strengths,
    pick_seed,
    prepare_init_image,
    resolve_output_dimensions,
    sanitize_llm_dimension,
    sd_error_message,
    validate_img2img_request,
)
from app.integrations.media_utils import (
    GENERATED_ROOT,
    generate_filename,
    generated_media_url,
    generated_thumb_url,
    make_thumbnail,
    resolve_trusted_generated_source,
    save_image_from_base64,
)
from app.integrations.sd_http import SdUnavailableError, sd_post_json
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
        "SD txt2img запрос: prompt=%r count=%d %dx%d steps=%d seed=%s url=%s",
        prompt[:80],
        max(1, min(10, int(count))),
        width,
        height,
        steps,
        seed,
        sd_base if (sd_base := resolve_sd_webui_url(sd_webui_url)) else settings.sd_webui_url,
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
        resp = sd_post_json(
            session,
            url,
            payload,
            timeout=settings.request_timeout,
            operation="txt2img",
        )
    except SdUnavailableError:
        elapsed = time.monotonic() - t0
        logger.error("SD txt2img недоступен за %.1fs (url=%s)", elapsed, url)
        raise
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
                "url": generated_media_url(filename, absolute=True, for_llm=True),
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


def _save_img2img_outputs(
    *,
    images_b64: list[str],
    prepared: PreparedInitImage,
    current_seed: int,
    denoising_strength: float,
    png_params: str,
    info_text: str,
    png_info_text: str,
    description: str,
) -> list[dict[str, str | int | float]]:
    """Сохранить результаты одного POST /img2img."""
    results: list[dict[str, str | int | float]] = []
    for img_b64 in images_b64:
        filename = save_image_from_base64(img_b64)
        thumb_name = make_thumbnail(filename)

        try:
            img_path = GENERATED_ROOT / filename
            with PILImage.open(img_path) as img:
                meta = PngImagePlugin.PngInfo()
                parameters_text = png_info_text or png_params
                if parameters_text:
                    meta.add_text("parameters", parameters_text)
                if info_text and info_text != parameters_text:
                    meta.add_text("info", info_text)
                if description:
                    meta.add_text("Description", description)
                meta.add_text("Init image", prepared.source_name)
                img.save(img_path, pnginfo=meta)
        except OSError as exc:
            logger.warning("Метаданные PNG для %s: %s", filename, exc)

        item: dict[str, str | int | float] = {
            "filename": filename,
            "url": generated_media_url(filename, absolute=True, for_llm=True),
            "seed": current_seed,
            "denoising_strength": denoising_strength,
        }
        if thumb_name:
            item["thumb_url"] = generated_thumb_url(thumb_name)
        results.append(item)
    return results


def _post_img2img_once(
    *,
    prepared: PreparedInitImage,
    prompt: str,
    negative_prompt: str,
    steps: int,
    out_w: int,
    out_h: int,
    cfg_scale: float,
    sampler_name: str,
    scheduler: str,
    seed: int,
    denoising_strength: float,
    restore_faces: bool,
    tiling: bool,
    resize_mode: int,
    description: str,
    sd_webui_url: str | None,
) -> tuple[list[dict[str, str | int | float]], str]:
    """Один запрос к SD img2img с заданным denoising_strength."""
    req = Img2ImgRequest(
        prompt=prompt,
        init_image_bytes=prepared.png_bytes,
        init_source_name=prepared.source_name,
        negative_prompt=negative_prompt,
        steps=steps,
        width=out_w,
        height=out_h,
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
    validate_img2img_request(req)
    current_seed = pick_seed(seed)
    init_b64 = encode_init_image_b64(prepared.png_bytes)
    payload = build_img2img_payload(
        req,
        init_b64=init_b64,
        width=out_w,
        height=out_h,
        seed=current_seed,
    )

    sd_base = resolve_sd_webui_url(sd_webui_url)
    session = get_sd_session()
    url = f"{sd_base}/sdapi/v1/img2img"
    logger.info(
        "img2img: %dx%d denoise=%.2f resize_mode=%d init=%s prompt=%r",
        out_w,
        out_h,
        denoising_strength,
        resize_mode,
        prepared.source_name,
        prompt[:80],
    )
    try:
        resp = sd_post_json(
            session,
            url,
            payload,
            timeout=settings.request_timeout,
            operation="img2img",
        )
    except SdUnavailableError as exc:
        logger.error("SD img2img недоступен (url=%s)", url)
        raise RuntimeError(str(exc)) from exc
    except requests.RequestException as exc:
        msg = sd_error_message(exc)
        logger.error("SD img2img ошибка: %s (url=%s)", msg, url)
        raise RuntimeError(f"SD img2img: {msg}") from exc

    data = resp.json()
    images_b64 = data.get("images", [])
    if not images_b64:
        logger.error("SD img2img: пустой images, keys=%s", list(data.keys()))
        raise RuntimeError("WebUI не вернул изображений")

    png_params, info_text = format_generation_meta(data)
    png_info_text = ""
    if info_text and not info_text.startswith("{"):
        png_info_text = info_text
    if not png_info_text and images_b64:
        try:
            info_resp = session.post(
                f"{sd_base}/sdapi/v1/png-info",
                json={"image": f"data:image/png;base64,{images_b64[0]}"},
                timeout=settings.request_timeout,
            )
            info_resp.raise_for_status()
            png_info_text = info_resp.json().get("info", "") or ""
        except requests.RequestException as exc:
            logger.warning("img2img png-info недоступен: %s", exc)

    items = _save_img2img_outputs(
        images_b64=images_b64,
        prepared=prepared,
        current_seed=current_seed,
        denoising_strength=denoising_strength,
        png_params=png_params,
        info_text=info_text,
        png_info_text=png_info_text,
        description=description,
    )
    return items, info_text or png_params


def img2img(
    prompt: str,
    init_image_url: str = "",
    *,
    init_image_bytes: bytes | None = None,
    init_source_name: str = "",
    negative_prompt: str = "",
    steps: int = 22,
    width: int = 0,
    height: int = 0,
    cfg_scale: float = 5.0,
    sampler_name: str = "Euler a",
    scheduler: str = "",
    seed: int = -1,
    denoising_strength: float = 0.54,
    denoising_strengths: list[float] | None = None,
    restore_faces: bool = False,
    tiling: bool = False,
    resize_mode: int = 0,
    description: str = "",
    sd_webui_url: str | None = None,
) -> str:
    """
    img2img: доработка существующего изображения через SD WebUI.

    init_image_url — URL или имя файла (если bytes не переданы из ToolExecutor).
    width/height = 0 — размер как у исходника (после prepare_init_image).
    denoising_strengths — несколько значений denoise с одним и тем же init (до 12).
    """
    if init_image_bytes is None:
        if not init_image_url.strip():
            raise ValueError("Укажите init_image_url или передайте init_image_bytes")
        init_path = resolve_trusted_generated_source(init_image_url)
        init_image_bytes = init_path.read_bytes()
        init_source_name = init_source_name or init_path.name

    strengths = normalize_denoising_strengths(denoising_strength, denoising_strengths)

    raw_w, raw_h = width, height
    width = sanitize_llm_dimension(width)
    height = sanitize_llm_dimension(height)
    if (raw_w, raw_h) != (width, height):
        logger.info(
            "img2img: размеры LLM %sx%s → %sx%s",
            raw_w,
            raw_h,
            width,
            height,
        )

    prepared = prepare_init_image(
        init_image_bytes,
        source_name=init_source_name,
    )
    out_w, out_h = resolve_output_dimensions(
        width,
        height,
        prepared.width,
        prepared.height,
    )

    t0 = time.monotonic()
    all_results: list[dict[str, str | int | float]] = []
    last_meta = ""
    for ds in strengths:
        items, last_meta = _post_img2img_once(
            prepared=prepared,
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=steps,
            out_w=out_w,
            out_h=out_h,
            cfg_scale=cfg_scale,
            sampler_name=sampler_name,
            scheduler=scheduler,
            seed=seed,
            denoising_strength=ds,
            restore_faces=restore_faces,
            tiling=tiling,
            resize_mode=resize_mode,
            description=description,
            sd_webui_url=sd_webui_url,
        )
        all_results.extend(items)

    denoise_label = (
        ", ".join(f"{v:g}" for v in strengths)
        if len(strengths) > 1
        else f"{strengths[0]:g}"
    )
    lines = [
        f"img2img завершён ({len(all_results)} изображений).",
        f"Prompt: {prompt}",
        f"Исходник: {prepared.source_name} ({prepared.width}×{prepared.height})",
        f"Выход: {out_w}×{out_h}, denoising_strength: {denoise_label}, resize_mode: {resize_mode}",
        "",
    ]
    for i, item in enumerate(all_results, 1):
        ds = item.get("denoising_strength")
        ds_note = f", denoise {ds:g}" if ds is not None else ""
        lines.append(f"Изображение {i} (seed {item['seed']}{ds_note}):")
        lines.append(f"  URL: {item['url']}")
        if item.get("thumb_url"):
            lines.append(f"  Thumbnail: {item['thumb_url']}")
        lines.append("")

    lines.extend(["--- Параметры генерации ---", last_meta])
    logger.info(
        "img2img готово за %.1fs (%d вариант(ов), init=%s)",
        time.monotonic() - t0,
        len(strengths),
        prepared.source_name,
    )
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
                resp = sd_post_json(
                    session,
                    f"{sd_base}/sdapi/v1/extra-single-image",
                    payload,
                    timeout=settings.request_timeout,
                    operation="upscale",
                )
                data = resp.json()
                upscaled_b64 = data.get("image")
                if upscaled_b64:
                    break
                last_error = "WebUI не вернул изображение"
            except SdUnavailableError as exc:
                last_error = str(exc)
                break
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
            "url": generated_media_url(filename, absolute=True, for_llm=True),
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
        width: int = 0,
        height: int = 0,
        cfg_scale: float = 5.0,
        sampler_name: str = "Euler a",
        scheduler: str = "",
        seed: int = -1,
        denoising_strength: float = 0.54,
        restore_faces: bool = False,
        tiling: bool = False,
        resize_mode: int = 0,
        description: str = "",
    ) -> str:
        """Доработать изображение (img2img). init_image_url — URL из чата; width/height 0 = размер исходника."""
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
