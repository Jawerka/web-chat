#!/usr/bin/env python3
"""
Long-running WD14 tagger worker (JSON-lines on stdin/stdout).

Запускается из wd-tagger venv web-chat сервисом; модель остаётся в RAM.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image


def _standalone_root(run_py: str) -> Path:
    return Path(run_py).resolve().parent


def _load_interrogator(model_key: str, threshold: float):
    from tagger.interrogator.interrogator import AbsInterrogator
    from tagger.interrogators import interrogators

    if model_key not in interrogators:
        raise KeyError(f"unknown model: {model_key}")
    interrogator = interrogators[model_key]

    dummy = Image.new("RGB", (64, 64), color=(128, 128, 128))
    _, tags = interrogator.interrogate(dummy)
    AbsInterrogator.postprocess_tags(
        tags,
        threshold=threshold,
        escape_tag=True,
        replace_underscore=True,
    )
    return interrogator, AbsInterrogator


def _tag_file(
    path: str,
    interrogator,
    postprocess_cls,
    threshold: float,
) -> str:
    image = Image.open(path)
    _, tags = interrogator.interrogate(image)
    filtered = postprocess_cls.postprocess_tags(
        tags,
        threshold=threshold,
        escape_tag=True,
        replace_underscore=True,
    )
    return ", ".join(filtered.keys())


def _write_response(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="WD14 tagger worker")
    parser.add_argument("--run-py", required=True, help="Path to wd14-tagger-standalone/run.py")
    parser.add_argument("--model", default="wd14-vit.v2")
    parser.add_argument("--threshold", type=float, default=0.35)
    args = parser.parse_args()

    root = str(_standalone_root(args.run_py))
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        interrogator, postprocess_cls = _load_interrogator(args.model, args.threshold)
    except Exception as exc:
        print(f"wd_tagger_worker: failed to load model: {exc}", file=sys.stderr)
        return 1

    print(
        f"wd_tagger_worker: ready model={args.model} threshold={args.threshold}",
        file=sys.stderr,
    )

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_response({"ok": False, "error": f"invalid json: {exc}"})
            continue

        cmd = msg.get("cmd")
        if cmd == "shutdown":
            _write_response({"ok": True})
            return 0
        if cmd == "ping":
            _write_response({"ok": True, "ready": True})
            continue
        if cmd == "tag":
            path = msg.get("path")
            if not path or not isinstance(path, str):
                _write_response({"ok": False, "error": "path required"})
                continue
            try:
                tags = _tag_file(path, interrogator, postprocess_cls, args.threshold)
                _write_response({"ok": True, "tags": tags})
            except Exception as exc:
                _write_response({"ok": False, "error": str(exc)})
            continue

        _write_response({"ok": False, "error": f"unknown cmd: {cmd!r}"})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
