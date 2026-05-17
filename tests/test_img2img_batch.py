"""Пакетный img2img: несколько denoise с одним init."""

from __future__ import annotations

import base64
import io

import pytest

from app.integrations.img2img_service import normalize_denoising_strengths

MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_normalize_denoising_strengths_list() -> None:
    assert normalize_denoising_strengths(0.54, [0.5, 0.62, 0.82]) == [0.5, 0.62, 0.82]


def test_normalize_denoising_strengths_single() -> None:
    assert normalize_denoising_strengths(0.54, None) == [0.54]


def test_img2img_multiple_denoise_same_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """Один вызов img2img с denoising_strengths → несколько POST с одним init."""
    from app.integrations import sd_tools as mod

    denoise_seen: list[float] = []
    init_b64_seen: list[str] = []

    def fake_post(*args, **kwargs) -> object:
        url = args[-1] if args else ""
        payload = kwargs.get("json") or {}

        class Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                if "/png-info" in url:
                    return {"info": "prompt test"}
                denoise_seen.append(float(payload.get("denoising_strength", -1)))
                init_b64_seen.append(payload.get("init_images", [""])[0])
                return {
                    "images": [base64.b64encode(MINIMAL_PNG).decode()],
                    "parameters": {},
                    "info": "{}",
                }

        return Resp()

    monkeypatch.setattr(mod, "get_sd_session", lambda: type("S", (), {"post": fake_post})())
    monkeypatch.setattr(mod, "resolve_sd_webui_url", lambda u=None: "http://test")
    monkeypatch.setattr(mod, "save_image_from_base64", lambda b: f"out_{len(denoise_seen)}.png")
    monkeypatch.setattr(mod, "make_thumbnail", lambda f: None)
    monkeypatch.setattr(
        mod,
        "generated_media_url",
        lambda f, absolute=False, for_llm=False: f"/media/generated/{f}",
    )

    raw = _make_png(512, 512)
    result = mod.img2img(
        "edit cat",
        init_image_bytes=raw,
        init_source_name="ref.png",
        denoising_strengths=[0.5, 0.62, 0.82],
    )
    assert denoise_seen == [0.5, 0.62, 0.82]
    assert len(init_b64_seen) == 3
    assert init_b64_seen[0] == init_b64_seen[1] == init_b64_seen[2]
    assert "img2img завершён (3 изображений)" in result
    assert "denoise 0.5" in result or "denoising_strength: 0.5" in result


def _make_png(w: int, h: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h)).save(buf, format="PNG")
    return buf.getvalue()
