"""
Извлечение метаданных Stable Diffusion из PNG (chunk parameters).
Порт логики из refs/meta-sd-to-markdown/main.py.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import BinaryIO

from PIL import Image


@dataclass(frozen=True, slots=True)
class SdMetadata:
    """Positive / negative prompt и строка параметров генерации."""

    prompt: str
    negative: str
    params: str

    @property
    def has_metadata(self) -> bool:
        return bool(self.prompt or self.negative or self.params)


def _parse_parameters_text(params_raw: str) -> SdMetadata | None:
    raw = (params_raw or "").strip()
    if not raw:
        return None

    lines = raw.splitlines()
    prompt_lines: list[str] = []
    negative_prompt = ""
    other_lines: list[str] = []
    in_negative = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.lower().startswith("negative prompt:"):
            in_negative = True
            negative_prompt = line.split(":", 1)[1].strip()
        elif in_negative and ":" not in line:
            negative_prompt += ", " + line
        elif in_negative and ":" in line:
            in_negative = False
            other_lines.append(line)
        elif not in_negative and ":" not in line:
            prompt_lines.append(line)
        else:
            other_lines.append(line)

    processed_other_lines = "\n".join(other_lines)
    if "Steps:" in processed_other_lines:
        pre, post = processed_other_lines.split("Steps:", 1)
        if pre:
            prompt_lines.append(pre.strip())
        processed_other_lines = "Steps:" + post.strip()

    return SdMetadata(
        prompt="\n".join(prompt_lines),
        negative=negative_prompt,
        params=processed_other_lines,
    )


def extract_sd_metadata_from_bytes(data: bytes) -> SdMetadata | None:
    """Прочитать parameters из PNG/JPEG/WebP в памяти."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            params_raw = (img.info.get("parameters") or "").strip()
            if not params_raw:
                return None
            return _parse_parameters_text(params_raw)
    except Exception:
        return None


def extract_sd_metadata_from_stream(stream: BinaryIO) -> SdMetadata | None:
    """Из файлового объекта (upload)."""
    data = stream.read()
    return extract_sd_metadata_from_bytes(data)
