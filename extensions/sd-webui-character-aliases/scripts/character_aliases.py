"""
Character Aliases (@alias) for SD WebUI / ReForge.

Local JSON storage, manual import from web-chat, prompt autocomplete, expansion before generate.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr
from modules import script_callbacks, scripts, shared

logger = logging.getLogger("character_aliases")

_MACRO_MENTION_RE = re.compile(r"@?@([a-zA-Z0-9_-]+)")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

CATEGORIES = [
    ("character", "Персонажи"),
    ("environment", "Окружение"),
    ("situation", "Ситуации"),
    ("other", "Прочее"),
]
CATEGORY_IDS = [c[0] for c in CATEGORIES]

_TAB_UI_BUILT = False


def _extension_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _data_file() -> Path:
    path = _extension_root() / "data" / "aliases.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
        url = str(getattr(shared.opts, "character_aliases_web_chat_url", "") or "").strip().rstrip("/")
        if url:
            return url
    except Exception:
        pass
    try:
        url = str(getattr(shared.opts, "web_chat_bridge_url", "") or "").strip().rstrip("/")
        if url:
            return url
    except Exception:
        pass
    return _default_web_chat_url()


def normalize_alias(value: str) -> str:
    raw = value.strip().lstrip("@").lower().replace(" ", "_")
    return raw


def validate_alias(value: str) -> str:
    alias = normalize_alias(value)
    if not alias or not _ALIAS_RE.match(alias):
        raise ValueError(
            "Alias: latin letters, digits, _ and - (2–63 chars), e.g. rainbow_dash",
        )
    return alias


def _normalize_macro_body(body: str) -> str:
    """Trim; strip one trailing comma+spaces if present."""
    text = (body or "").strip()
    if not text:
        return text
    return re.sub(r",\s*$", "", text)


def expand_macro_text(text: str, alias_to_body: dict[str, str]) -> str:
    if not text or not alias_to_body:
        return text

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        raw = alias_to_body.get(key)
        if raw is None:
            return match.group(0)
        return _normalize_macro_body(raw)

    return _MACRO_MENTION_RE.sub(repl, text)


def load_aliases() -> list[dict]:
    path = _data_file()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("character-aliases: failed to read %s: %s", path, exc)
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _publish_alias_files() -> None:
    """Expose aliases.json path for JS via A1111 file= route (no extra FastAPI)."""
    try:
        from modules import paths

        tmp_dir = Path(paths.script_path) / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        rel = os.path.relpath(_data_file(), paths.script_path).replace("\\", "/")
        (tmp_dir / "character_aliases_path.txt").write_text(rel, encoding="utf-8")
        enabled = "1"
        try:
            if not getattr(shared.opts, "character_aliases_autocomplete_enabled", True):
                enabled = "0"
        except Exception:
            pass
        (tmp_dir / "character_aliases_autocomplete.txt").write_text(enabled, encoding="utf-8")
    except Exception as exc:
        logger.debug("character-aliases: publish files failed: %s", exc)


def save_aliases(items: list[dict]) -> None:
    path = _data_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, path)
    _publish_alias_files()


def load_aliases_map() -> dict[str, str]:
    return {
        str(item.get("alias", "")).lower(): str(item.get("body") or "")
        for item in load_aliases()
        if item.get("alias")
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def import_from_webchat() -> tuple[list[dict], str]:
    base = get_web_chat_url()
    url = f"{base}/api/prompt-macros"
    logger.info("character-aliases: import GET %s", url)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"web-chat HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach web-chat at {base}: {exc.reason}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("web-chat returned invalid JSON") from exc

    if not isinstance(payload, list):
        raise RuntimeError("web-chat returned unexpected payload")

    items: list[dict] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        alias = str(row.get("alias") or "").strip()
        if not alias:
            continue
        items.append(
            {
                "id": str(row.get("id") or uuid.uuid4()),
                "alias": alias,
                "label": str(row.get("label") or "").strip(),
                "body": str(row.get("body") or "").strip(),
                "category": str(row.get("category") or "character"),
                "updated_at": _utc_now_iso(),
            },
        )

    save_aliases(items)
    return items, f"Imported {len(items)} aliases from web-chat."


def _filter_by_category(items: list[dict], category: str) -> list[dict]:
    cat = (category or "character").strip().lower()
    return [item for item in items if str(item.get("category") or "other") == cat]


def _table_rows(items: list[dict], category: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in _filter_by_category(items, category):
        body = str(item.get("body") or "")
        preview = body if len(body) <= 80 else body[:77] + "..."
        rows.append(
            [
                str(item.get("alias") or ""),
                str(item.get("label") or ""),
                preview,
            ],
        )
    return rows


def _find_by_alias(items: list[dict], alias: str) -> dict | None:
    key = normalize_alias(alias)
    for item in items:
        if normalize_alias(str(item.get("alias") or "")) == key:
            return item
    return None


def _category_label_from_id(category_id: str) -> str:
    for cid, label in CATEGORIES:
        if cid == category_id:
            return label
    return CATEGORIES[0][1]


def _alias_choices(category: str) -> list[str]:
    return [
        str(item.get("alias") or "")
        for item in _filter_by_category(load_aliases(), category)
        if item.get("alias")
    ]


def _ui_import() -> tuple:
    cid = _default_category()
    cat_label = _category_label_from_id(cid)
    empty_picker = gr.update(choices=[], value=None)
    try:
        items, msg = import_from_webchat()
    except RuntimeError as exc:
        return [], str(exc), "", "", "", cat_label, empty_picker
    rows = _table_rows(items, cid)
    picker = gr.update(choices=_alias_choices(cid), value=None)
    return rows, msg, "", "", "", cat_label, picker


def _ui_refresh(category_label: str) -> tuple:
    cid = _category_id_from_label(category_label)
    rows = _table_rows(load_aliases(), cid)
    choices = _alias_choices(cid)
    return rows, gr.update(choices=choices, value=None), "", "", "", ""


def _category_id_from_label(label: str) -> str:
    for cid, clabel in CATEGORIES:
        if clabel == label:
            return cid
    return "character"


def _default_category() -> str:
    try:
        cat = str(getattr(shared.opts, "character_aliases_default_category", "character") or "character")
        return cat if cat in CATEGORY_IDS else "character"
    except Exception:
        return "character"



def _ui_select_row(category: str, alias: str) -> tuple[str, str, str]:
    item = _find_by_alias(load_aliases(), alias)
    if item is None:
        return "", "", ""
    return (
        str(item.get("alias") or ""),
        str(item.get("label") or ""),
        str(item.get("body") or ""),
    )


def _ui_new_row() -> tuple[str, str, str, str]:
    return "", "", "", ""


def _ui_save(
    category: str,
    alias: str,
    label: str,
    body: str,
    previous_alias: str,
) -> tuple:
    body = (body or "").strip()
    if not body:
        return (
            _table_rows(load_aliases(), category),
            "Tags (body) cannot be empty.",
            alias,
            label,
            body,
            gr.update(),
        )
    try:
        alias_norm = validate_alias(alias)
    except ValueError as exc:
        return (
            _table_rows(load_aliases(), category),
            str(exc),
            alias,
            label,
            body,
            gr.update(),
        )

    items = load_aliases()
    prev_key = normalize_alias(previous_alias) if previous_alias else ""
    existing = _find_by_alias(items, alias_norm)
    if prev_key and prev_key != alias_norm:
        old = _find_by_alias(items, prev_key)
        if old is not None:
            items = [i for i in items if i is not old]

    if existing is not None and (not prev_key or prev_key == alias_norm):
        existing["alias"] = alias_norm
        existing["label"] = (label or "").strip()
        existing["body"] = body
        existing["category"] = category if category in CATEGORY_IDS else "character"
        existing["updated_at"] = _utc_now_iso()
        msg = f"Saved @{alias_norm}."
    else:
        if _find_by_alias(items, alias_norm):
            return (
                _table_rows(items, category),
                f"Alias @{alias_norm} already exists.",
                alias_norm,
                label,
                body,
                gr.update(),
            )
        items.append(
            {
                "id": str(uuid.uuid4()),
                "alias": alias_norm,
                "label": (label or "").strip(),
                "body": body,
                "category": category if category in CATEGORY_IDS else "character",
                "updated_at": _utc_now_iso(),
            },
        )
        msg = f"Created @{alias_norm}."

    save_aliases(items)
    picker = gr.update(choices=_alias_choices(category), value=alias_norm)
    return _table_rows(items, category), msg, alias_norm, alias_norm, body, picker


def _ui_delete(category: str, alias: str) -> tuple:
    if not alias:
        return (
            _table_rows(load_aliases(), category),
            "Select an alias to delete.",
            "",
            "",
            "",
            gr.update(),
        )
    key = normalize_alias(alias)
    items = load_aliases()
    new_items = [i for i in items if normalize_alias(str(i.get("alias") or "")) != key]
    if len(new_items) == len(items):
        return (
            _table_rows(items, category),
            f"Alias @{key} not found.",
            alias,
            "",
            "",
            gr.update(),
        )
    save_aliases(new_items)
    picker = gr.update(choices=_alias_choices(category), value=None)
    return _table_rows(new_items, category), f"Deleted @{key}.", "", "", "", picker


def _on_ui_settings():
    section = ("character_aliases", "Character Aliases")
    shared.opts.add_option(
        "character_aliases_web_chat_url",
        shared.OptionInfo(
            "",
            "Web-Chat base URL (import)",
            gr.Textbox,
            {"placeholder": "http://192.168.88.44:8090 (empty = Web-Chat Bridge URL)"},
            section=section,
        ),
    )
    shared.opts.add_option(
        "character_aliases_expand_enabled",
        shared.OptionInfo(
            True,
            "Expand @alias before generation",
            gr.Checkbox,
            section=section,
        ),
    )
    shared.opts.add_option(
        "character_aliases_autocomplete_enabled",
        shared.OptionInfo(
            True,
            "Autocomplete @alias in positive prompt",
            gr.Checkbox,
            section=section,
        ),
    )
    shared.opts.add_option(
        "character_aliases_default_category",
        shared.OptionInfo(
            "character",
            "Default category tab",
            gr.Dropdown,
            {"choices": CATEGORY_IDS},
            section=section,
        ),
    )
    _publish_alias_files()


def on_ui_tabs():
    global _TAB_UI_BUILT
    if _TAB_UI_BUILT:
        return []
    _TAB_UI_BUILT = True

    with gr.Blocks() as tab:
        gr.Markdown(
            "## Character Aliases (@alias)\n"
            "Локальный каталог тегов персонажей. **Import from web-chat** загружает "
            "`/api/prompt-macros` в `data/aliases.json`. Редактирование сохраняется только локально."
        )
        with gr.Row():
            with gr.Column(scale=1):
                category_filter = gr.Radio(
                    choices=[label for _id, label in CATEGORIES],
                    value=CATEGORIES[0][1],
                    label="Category",
                    elem_id="character_aliases_category",
                )
                alias_table = gr.Dataframe(
                    headers=["Alias", "Label", "Tags preview"],
                    datatype=["str", "str", "str"],
                    interactive=False,
                    wrap=True,
                    elem_id="character_aliases_table",
                )
                alias_picker = gr.Dropdown(
                    label="Select alias to edit",
                    choices=[],
                    elem_id="character_aliases_picker",
                )
                with gr.Row():
                    btn_import = gr.Button("Import from web-chat", variant="primary")
                    btn_new = gr.Button("New")
                with gr.Row():
                    btn_save = gr.Button("Save")
                    btn_delete = gr.Button("Delete", variant="stop")
                status = gr.Textbox(
                    label="Status",
                    interactive=False,
                    lines=2,
                    elem_id="character_aliases_status",
                )
            with gr.Column(scale=1):
                alias_input = gr.Textbox(
                    label="Alias",
                    placeholder="rainbow_dash",
                    elem_id="character_aliases_alias",
                )
                label_input = gr.Textbox(
                    label="Label",
                    placeholder="Rainbow Dash",
                    elem_id="character_aliases_label",
                )
                body_input = gr.Textbox(
                    label="Tags (body)",
                    lines=14,
                    placeholder="rainbow_dash, pegasus, wings, ...",
                    elem_id="character_aliases_body",
                )
                selected_alias = gr.Textbox(visible=False, elem_id="character_aliases_selected")

        def _on_category(label: str):
            return _ui_refresh(label)

        def _on_picker(alias: str, category_label: str):
            if not alias:
                return "", "", "", ""
            cid = _category_id_from_label(category_label)
            a, lbl, b = _ui_select_row(cid, alias)
            return a, lbl, b, a

        category_filter.change(
            fn=_on_category,
            inputs=[category_filter],
            outputs=[alias_table, alias_picker, alias_input, label_input, body_input, status],
        )

        alias_picker.change(
            fn=_on_picker,
            inputs=[alias_picker, category_filter],
            outputs=[alias_input, label_input, body_input, selected_alias],
        )

        btn_import.click(
            fn=_ui_import,
            inputs=None,
            outputs=[
                alias_table,
                status,
                alias_input,
                label_input,
                body_input,
                category_filter,
                alias_picker,
            ],
        ).then(
            fn=None,
            _js="characterAliasesReloadList",
            inputs=None,
            outputs=None,
        )

        btn_new.click(
            fn=_ui_new_row,
            inputs=None,
            outputs=[alias_input, label_input, body_input, selected_alias],
        )

        btn_save.click(
            fn=lambda cat_label, alias, label, body, prev: _ui_save(
                _category_id_from_label(cat_label),
                alias,
                label,
                body,
                prev,
            ),
            inputs=[category_filter, alias_input, label_input, body_input, selected_alias],
            outputs=[alias_table, status, alias_input, selected_alias, body_input, alias_picker],
        ).then(
            fn=None,
            _js="characterAliasesReloadList",
            inputs=None,
            outputs=None,
        )

        btn_delete.click(
            fn=lambda cat_label, alias: _ui_delete(_category_id_from_label(cat_label), alias),
            inputs=[category_filter, alias_input],
            outputs=[alias_table, status, alias_input, label_input, body_input, alias_picker],
        ).then(
            fn=None,
            _js="characterAliasesReloadList",
            inputs=None,
            outputs=None,
        )

        def _initial_load():
            cid = _default_category()
            label = _category_label_from_id(cid)
            return (
                _table_rows(load_aliases(), cid),
                gr.update(choices=_alias_choices(cid), value=None),
                "",
            )

        tab.load(
            fn=_initial_load,
            inputs=None,
            outputs=[alias_table, alias_picker, status],
        )

    return [(tab, "Character Aliases", "character_aliases_tab")]


script_callbacks.on_ui_settings(_on_ui_settings)
script_callbacks.on_ui_tabs(on_ui_tabs)

try:
    _publish_alias_files()
except Exception:
    pass


class CharacterAliasExpandScript(scripts.Script):
    def title(self):
        return "Character Aliases"

    def show(self, is_img2img):
        return False

    def process(self, p, *args):
        try:
            if not getattr(shared.opts, "character_aliases_expand_enabled", True):
                return
        except Exception:
            pass
        alias_map = load_aliases_map()
        if not alias_map:
            return
        p.all_prompts = [expand_macro_text(t, alias_map) for t in p.all_prompts]
        if hasattr(p, "all_hr_prompts") and p.all_hr_prompts:
            p.all_hr_prompts = [expand_macro_text(t, alias_map) for t in p.all_hr_prompts]
