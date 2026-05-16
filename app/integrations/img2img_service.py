"""
Сервис img2img для Stable Diffusion WebUI (POST /sdapi/v1/img2img).

Подготовка исходника, валидация параметров, сборка payload и разбор ответа API.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import random
from dataclasses import dataclass
from typing import Any

import requests
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

# SD WebUI resize_mode: 0 Just resize, 1 Crop and resize, 2 Resize and fill, 3 Just resize (latent upscale)
RESIZE_MODE_LABELS: dict[int, str] = {
    0: "just_resize",
    1: "crop_and_resize",
    2: "resize_and_fill",
    3: "latent_upscale",
}

DIM_MIN = 512
DIM_MAX = 2048
DIM_ALIGN = 8
DEFAULT_DENOISING = 0.52
LLM_DENOISING_MIN = 0.20
LLM_DENOISING_MAX = 0.92


@dataclass(frozen=True)
class PreparedInitImage:
    """Исходник, готовый к отправке в SD."""

    png_bytes: bytes
    width: int
    height: int
    source_name: str


@dataclass
class Img2ImgRequest:
    """Параметры одного вызова img2img."""

    prompt: str
    init_image_bytes: bytes
    init_source_name: str = ""
    negative_prompt: str = ""
    steps: int = 22
    width: int = 0
    height: int = 0
    cfg_scale: float = 5.0
    sampler_name: str = ""
    scheduler: str = ""
    seed: int = -1
    denoising_strength: float = DEFAULT_DENOISING
    restore_faces: bool = False
    tiling: bool = False
    resize_mode: int = 0
    description: str = ""


def prepare_init_image(
    raw_bytes: bytes,
    *,
    source_name: str = "",
    max_side: int | None = None,
) -> PreparedInitImage:
    """
    Нормализовать исходник: RGB/RGBA → PNG, при необходимости уменьшить длинную сторону.

    SD WebUI стабильнее работает с умеренным размером init; огромные PNG из чата режем.
    """
    limit = max_side if max_side is not None else DIM_MAX
    with Image.open(io.BytesIO(raw_bytes)) as img:
        img = _ensure_rgb(img)
        w, h = img.size
        if max(w, h) > limit:
            resized = img.copy()
            resized.thumbnail((limit, limit), Image.Resampling.LANCZOS)
            img = resized
            w, h = img.size
            logger.info(
                "img2img init уменьшен до %dx%d (max_side=%d), источник=%s",
                w,
                h,
                limit,
                source_name or "?",
            )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    return PreparedInitImage(
        png_bytes=png_bytes,
        width=w,
        height=h,
        source_name=source_name or "init.png",
    )


def sanitize_llm_dimension(value: int | float | str | None) -> int:
    """
    Нормализовать width/height из аргументов LLM.

    0 — взять размер с исходника; иначе clamp 512–2048, кратно 8.
    Некорректные значения (400, 3000, не кратные 8) приводятся к допустимым.
    """
    try:
        v = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    return _clamp_dim(v)


def resolve_output_dimensions(
    width: int,
    height: int,
    source_width: int,
    source_height: int,
) -> tuple[int, int]:
    """
    Вычислить width/height для payload.

    0 (или отрицательное) по оси — взять размер исходника после prepare_init_image.
  Иначе — clamp 512–2048, кратно 8.
    """
    if width <= 0:
        out_w = _dim_from_source(source_width)
    else:
        out_w = _clamp_dim(width)

    if height <= 0:
        out_h = _dim_from_source(source_height)
    else:
        out_h = _clamp_dim(height)

    return out_w, out_h


def validate_img2img_request(req: Img2ImgRequest) -> None:
    """Проверить параметры до обращения к SD."""
    if not req.prompt.strip():
        raise ValueError("prompt не может быть пустым")
    if not req.init_image_bytes:
        raise ValueError("init_image_bytes пуст")

    if not (1 <= req.steps <= 150):
        raise ValueError("steps должен быть от 1 до 150")
    if not (1 <= req.cfg_scale <= 30):
        raise ValueError("cfg_scale должен быть от 1 до 30")
    if not (0.0 <= req.denoising_strength <= 1.0):
        raise ValueError("denoising_strength должен быть от 0.0 до 1.0")
    if req.resize_mode not in RESIZE_MODE_LABELS:
        raise ValueError(
            f"resize_mode должен быть 0–3 ({', '.join(f'{k}={v}' for k, v in RESIZE_MODE_LABELS.items())})"
        )

    if req.width > 0:
        _assert_dim("width", req.width)
    if req.height > 0:
        _assert_dim("height", req.height)


def build_img2img_payload(
    req: Img2ImgRequest,
    *,
    init_b64: str,
    width: int,
    height: int,
    seed: int,
) -> dict[str, Any]:
    """Собрать JSON для POST /sdapi/v1/img2img (Automatic1111 / Forge)."""
    return {
        "prompt": req.prompt,
        "negative_prompt": req.negative_prompt or settings.sd_negative_prompt,
        "steps": req.steps,
        "width": width,
        "height": height,
        "cfg_scale": req.cfg_scale,
        "sampler_name": req.sampler_name or settings.sd_sampler,
        "scheduler": req.scheduler or settings.sd_schedule_type,
        "seed": seed,
        "batch_size": 1,
        "n_iter": 1,
        "tiling": req.tiling,
        "restore_faces": req.restore_faces,
        "init_images": [init_b64],
        "resize_mode": req.resize_mode,
        "denoising_strength": req.denoising_strength,
        "send_images": True,
        "save_images": False,
    }


def pick_seed(seed: int) -> int:
    """Случайный seed, если передан -1."""
    return seed if seed != -1 else random.randint(0, 2**32 - 1)


def encode_init_image_b64(png_bytes: bytes) -> str:
    """Base64 без data:-префикса (требование SD API)."""
    return base64.b64encode(png_bytes).decode("utf-8")


def format_generation_meta(data: dict[str, Any]) -> tuple[str, str]:
    """
    Извлечь parameters и info из ответа SD для PNG metadata.

    Returns:
        (parameters_text, info_text)
    """
    parameters = data.get("parameters", {})
    info_text = data.get("info", "")
    if isinstance(parameters, str):
        png_params = parameters
    else:
        png_params = json.dumps(parameters, ensure_ascii=False, indent=2)
    return png_params, str(info_text) if info_text else ""


def sd_error_message(exc: requests.RequestException) -> str:
    """Человекочитаемое сообщение из ответа WebUI."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return str(exc)
    try:
        body = resp.json()
        if isinstance(body, dict):
            detail = body.get("detail") or body.get("error") or body.get("message")
            if detail:
                return f"{resp.status_code}: {detail}"
    except (ValueError, json.JSONDecodeError):
        pass
    text = (resp.text or "").strip()
    if text and len(text) < 500:
        return f"{resp.status_code}: {text}"
    return f"{resp.status_code} {resp.reason}"


def _clamp_dim(value: int) -> int:
    v = max(DIM_MIN, min(DIM_MAX, int(value)))
    return (v // DIM_ALIGN) * DIM_ALIGN


def _dim_from_source(value: int) -> int:
    """Размер из исходника: кратно 8, в пределах DIM_MIN..DIM_MAX."""
    v = max(1, min(DIM_MAX, int(value)))
    aligned = (v // DIM_ALIGN) * DIM_ALIGN
    return max(DIM_MIN, aligned)


def _assert_dim(name: str, value: int) -> None:
    if not (DIM_MIN <= value <= DIM_MAX):
        raise ValueError(f"{name} должен быть от {DIM_MIN} до {DIM_MAX}")
    if value % DIM_ALIGN != 0:
        raise ValueError(f"{name} должен быть кратен {DIM_ALIGN}")


def _ensure_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "P", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        return background
    if img.mode != "RGB":
        return img.convert("RGB")
    return img
