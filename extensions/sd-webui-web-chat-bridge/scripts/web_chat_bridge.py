"""
web-chat → SD WebUI bridge: queue PNG + infotext from web-chat, paste into img2img.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import json
import logging
import threading
import time
import urllib.error
import urllib.request

import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from modules import images, script_callbacks, scripts, shared
from modules.infotext_utils import ParamBinding, register_paste_params_button

logger = logging.getLogger("web_chat_bridge")

_BRIDGE_UI_BUILT = False
_BRIDGE_PASTE_REGISTERED = False

QUEUE_TTL_SEC = 3600
_WAIT_POLL_SEC = 0.35

_queue_lock = threading.Lock()
_queue_notify = threading.Condition(_queue_lock)
_queued_import: dict | None = None


def _default_web_chat_url() -> str:
    try:
        url = str(getattr(shared.cmd_opts, "web_chat_url", "") or "").strip().rstrip("/")
        if url:
            return url
    except Exception:
        pass
    return "http://192.168.88.44:8090"


def get_web_chat_url() -> str:
    try:
        url = str(getattr(shared.opts, "web_chat_bridge_url", "") or "").strip().rstrip("/")
        if url:
            return url
    except Exception:
        pass
    return _default_web_chat_url()


def _purge_stale_queue_locked(now: float) -> None:
    global _queued_import
    if _queued_import is None:
        return
    if now - float(_queued_import.get("created_at") or 0) > QUEUE_TTL_SEC:
        _queued_import = None


def _decode_image_b64(image_b64: str) -> object:
    image_bytes = base64.b64decode(image_b64, validate=False)
    return images.read(io.BytesIO(image_bytes))


def queue_import_payload(
    *,
    image_base64: str,
    infotext: str,
    filename: str,
    mime: str = "image/png",
    pil_image: object | None = None,
) -> str:
    global _queued_import
    infotext = (infotext or "").strip()
    if not image_base64 or not infotext:
        raise ValueError("image_base64 and infotext required")

    if pil_image is None:
        pil_image = _decode_image_b64(image_base64)

    now = time.time()
    with _queue_notify:
        _purge_stale_queue_locked(now)
        _queued_import = {
            "pil_image": pil_image,
            "infotext": infotext,
            "filename": filename or "web-chat-import.png",
            "mime": mime or "image/png",
            "created_at": now,
        }
        _queue_notify.notify_all()
    logger.info("web-chat bridge: queued %s", filename)
    return filename


def peek_queued_import() -> dict | None:
    with _queue_lock:
        _purge_stale_queue_locked(time.time())
        if _queued_import is None:
            return None
        return {
            "filename": _queued_import.get("filename"),
            "created_at": _queued_import.get("created_at"),
        }


def take_queued_import() -> dict | None:
    global _queued_import
    with _queue_lock:
        _purge_stale_queue_locked(time.time())
        if _queued_import is None:
            return None
        data = dict(_queued_import)
        _queued_import = None
        return data


def wait_for_queued_import(timeout_sec: float = 20.0) -> dict | None:
    """Block until queue has an item or timeout (long-poll helper)."""
    deadline = time.monotonic() + max(0.5, min(timeout_sec, 55.0))
    with _queue_notify:
        while True:
            _purge_stale_queue_locked(time.time())
            if _queued_import is not None:
                return {
                    "filename": _queued_import.get("filename"),
                    "created_at": _queued_import.get("created_at"),
                }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            _queue_notify.wait(timeout=min(_WAIT_POLL_SEC, remaining))


def _apply_queued_import() -> tuple[object, str, str, str]:
    data = take_queued_import()
    if data is None:
        logger.debug("web-chat bridge: apply called but queue empty")
        return gr.update(), gr.update(), "Очередь пуста.", "0"

    pil_image = data.get("pil_image")
    infotext = (data.get("infotext") or "").strip()
    filename = data.get("filename") or "web-chat-import.png"
    if pil_image is None or not infotext:
        logger.warning("web-chat bridge: queued import incomplete for %s", filename)
        return gr.update(), gr.update(), "Queued import is incomplete.", "0"

    logger.info("web-chat bridge: applying queued %s", filename)
    return pil_image, infotext, f"Applied {filename} from web-chat queue.", "1"


def fetch_import_payload(token: str) -> tuple[object | None, str, str]:
    """Legacy: fetch by one-time token from web-chat."""
    token = (token or "").strip()
    if not token:
        return None, "", "No import token."

    base = get_web_chat_url()
    url = f"{base}/api/sd-bridge/import/{token}"
    logger.info("web-chat bridge: legacy GET %s", url)

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.warning("web-chat bridge HTTP %s: %s", exc.code, detail)
        return None, "", f"web-chat HTTP {exc.code}: {detail or exc.reason}"
    except urllib.error.URLError as exc:
        logger.warning("web-chat bridge URL error: %s", exc)
        return None, "", f"Cannot reach web-chat at {base}: {exc.reason}"
    except Exception as exc:
        logger.exception("web-chat bridge fetch failed")
        return None, "", f"Import failed: {exc}"

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None, "", "web-chat returned invalid JSON."

    if not isinstance(payload, dict):
        return None, "", "web-chat returned unexpected payload."

    image_b64 = payload.get("image_base64") or payload.get("image") or ""
    infotext = (payload.get("infotext") or payload.get("info") or "").strip()
    filename = payload.get("filename") or "web-chat-import.png"

    if not image_b64 or not infotext:
        return None, "", "web-chat payload incomplete."

    try:
        pil_image = _decode_image_b64(image_b64)
    except Exception as exc:
        return None, "", f"Invalid image data: {exc}"

    return pil_image, infotext, f"Loaded {filename} from web-chat."


def _fetch_import_payload(token: str) -> tuple[object, str, str, str]:
    pil_image, infotext, status = fetch_import_payload(token)
    if pil_image is None:
        return gr.update(), gr.update(), status, "0"
    return pil_image, infotext, status, "1"


def _client_ip_allowed(client_host: str | None) -> bool:
    if not client_host:
        return False
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


def _on_ui_settings():
    section = ("web_chat_bridge", "Web-Chat Bridge")
    opt = shared.OptionInfo(
        _default_web_chat_url(),
        "Web-Chat base URL",
        gr.Textbox,
        {"placeholder": "http://192.168.88.44:8090"},
        section=section,
    )
    opt.info("Base URL of web-chat server for gallery → img2img imports.")
    shared.opts.add_option("web_chat_bridge_url", opt)


def _on_app_started(_demo, app: FastAPI):
    @app.get("/web-chat-bridge/ping")
    def ping():
        pending = peek_queued_import()
        return {
            "ok": True,
            "web_chat_url": get_web_chat_url(),
            "pending": pending is not None,
            "pending_filename": (pending or {}).get("filename"),
        }

    @app.get("/web-chat-bridge/pending")
    def pending():
        item = peek_queued_import()
        if item is None:
            return {"pending": False}
        return {"pending": True, "filename": item.get("filename")}

    @app.get("/web-chat-bridge/wait-pending")
    def wait_pending(timeout: float = 20.0):
        item = wait_for_queued_import(timeout_sec=timeout)
        if item is None:
            return {"pending": False}
        return {"pending": True, "filename": item.get("filename")}

    @app.post("/web-chat-bridge/push")
    async def push_import(request: Request):
        if not _client_ip_allowed(request.client.host if request.client else None):
            return JSONResponse(
                status_code=403,
                content={"ok": False, "detail": "push allowed from LAN only"},
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "detail": "invalid JSON"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"ok": False, "detail": "invalid body"})
        try:
            image_b64 = str(body.get("image_base64") or "")
            infotext = str(body.get("infotext") or "")
            filename = str(body.get("filename") or "web-chat-import.png")
            mime = str(body.get("mime") or "image/png")
            pil_image = _decode_image_b64(image_b64)
            filename = queue_import_payload(
                image_base64=image_b64,
                infotext=infotext,
                filename=filename,
                mime=mime,
                pil_image=pil_image,
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "detail": str(exc)})
        except Exception as exc:
            logger.exception("web-chat bridge push decode failed")
            return JSONResponse(status_code=400, content={"ok": False, "detail": str(exc)})
        return {"ok": True, "queued": True, "filename": filename}


script_callbacks.on_ui_settings(_on_ui_settings)
script_callbacks.on_app_started(_on_app_started)


class WebChatBridgeScript(scripts.Script):
    def title(self):
        return "Web-Chat Bridge"

    def show(self, is_img2img):
        if is_img2img:
            return scripts.AlwaysVisible
        return False

    def ui(self, is_img2img):
        global _BRIDGE_UI_BUILT, _BRIDGE_PASTE_REGISTERED

        if _BRIDGE_UI_BUILT:
            return []

        _BRIDGE_UI_BUILT = True

        with gr.Accordion(
            "Web-Chat Bridge (gallery import)",
            open=True,
            elem_id="web_chat_bridge_panel",
        ):
            gr.Markdown(
                "Галерея web-chat отправляет сюда PNG и параметры. "
                "На **img2img** импорт применяется автоматически, когда очередь не пуста."
            )
            bridge_status = gr.Textbox(
                label="Status",
                interactive=False,
                lines=3,
                elem_id="web_chat_bridge_status",
            )
            bridge_token = gr.Textbox(
                label="Legacy token (optional)",
                visible=False,
                lines=1,
                elem_id="web_chat_bridge_token",
            )
            # Hidden Image — совместимо с register_paste_params_button (ReForge).
            bridge_image = gr.Image(
                label="Import image",
                visible=False,
                type="pil",
                elem_id="web_chat_bridge_image",
            )
            bridge_info = gr.Textbox(
                label="Import infotext",
                visible=False,
                lines=12,
                elem_id="web_chat_bridge_info",
            )
            bridge_apply = gr.Button(
                "Apply queued import",
                visible=False,
                elem_id="web_chat_bridge_apply_queued",
            )
            bridge_fetch = gr.Button(
                "Fetch from web-chat (legacy token)",
                visible=False,
                elem_id="web_chat_bridge_fetch",
            )
            bridge_send_i2i = gr.Button(
                "Send to img2img",
                visible=False,
                elem_id="web_chat_bridge_send_i2i",
            )
            bridge_paste_ok = gr.Textbox(
                value="0",
                visible=False,
                elem_id="web_chat_bridge_paste_ok",
            )

        if not _BRIDGE_PASTE_REGISTERED:
            register_paste_params_button(
                ParamBinding(
                    paste_button=bridge_send_i2i,
                    tabname="img2img",
                    source_text_component=bridge_info,
                    source_image_component=bridge_image,
                )
            )
            _BRIDGE_PASTE_REGISTERED = True

        bridge_apply.click(
            fn=_apply_queued_import,
            inputs=None,
            outputs=[bridge_image, bridge_info, bridge_status, bridge_paste_ok],
            show_progress=False,
        ).then(
            fn=None,
            _js="webChatBridgeAfterApply",
            inputs=[bridge_paste_ok],
            outputs=None,
            show_progress=False,
        )

        bridge_fetch.click(
            fn=_fetch_import_payload,
            inputs=[bridge_token],
            outputs=[bridge_image, bridge_info, bridge_status, bridge_paste_ok],
            show_progress=False,
        ).then(
            fn=None,
            _js="webChatBridgeAfterApply",
            inputs=[bridge_paste_ok],
            outputs=None,
            show_progress=False,
        )

        return (
            bridge_status,
            bridge_token,
            bridge_image,
            bridge_info,
            bridge_apply,
            bridge_fetch,
            bridge_send_i2i,
            bridge_paste_ok,
        )
