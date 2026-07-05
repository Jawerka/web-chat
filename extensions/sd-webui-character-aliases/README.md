# sd-webui-character-aliases

Расширение для **Stable Diffusion WebUI** (A1111 / Forge / **ReForge**): `@alias` для positive prompt — автоподсказка и подстановка тегов персонажей.

Расположение в репозитории: **`extensions/sd-webui-character-aliases/`**

## Возможности

- Вкладка **Character Aliases** — просмотр и локальное редактирование алиасов (alias, label, tags/body)
- **Import from web-chat** — однократная загрузка `GET /api/prompt-macros` в `data/aliases.json` (без автосинхронизации)
- **Autocomplete** при `@` в positive prompt (txt2img / img2img) — вставляет теги из `body`, не `@alias`
- **Expand before Generate** — оставшиеся `@alias` в промпте разворачиваются перед генерацией

Пример: `@rainbow dash` → подсказка `rainbow_dash` → в промпт вставляется полный список тегов.

## Требования

- SD WebUI / ReForge на `:7860`
- web-chat с `/api/prompt-macros` (для импорта)
- SD-хост (например `192.168.88.52`) в trusted internal web-chat (через `SD_WEBUI_URL` в `.env`)

## Установка

```bash
# на хосте SD (.52)
cd /path/to/stable-diffusion-webui/extensions
ln -s /path/to/web-chat/extensions/sd-webui-character-aliases sd-webui-character-aliases
```

**Apply and restart UI** в SD WebUI.

## Настройка

| Способ | Параметр | Пример |
|--------|----------|--------|
| Settings → Character Aliases | Web-Chat base URL | `http://192.168.88.44:8090` |
| Settings → Web-Chat Bridge | (fallback URL) | тот же URL |
| CLI (`webui-user.sh`) | `--web-chat-url` | задаётся расширением **web-chat-bridge** (общий для обоих) |

Приоритет URL: **Character Aliases** → **Web-Chat Bridge** → **CLI bridge** → default.

Другие опции:

- **Expand @alias before generation** — подстановка перед Generate (по умолчанию вкл.)
- **Autocomplete @alias in positive prompt** — автоподсказка (по умолчанию вкл.)
- **Default category tab** — категория по умолчанию на вкладке

## Первый запуск

1. Settings → укажите URL web-chat (если не задан у bridge).
2. Вкладка **Character Aliases** → **Import from web-chat**.
3. В txt2img positive prompt наберите `@rainbow` → выберите алиас → теги вставятся в промпт.

Данные хранятся в `extensions/sd-webui-character-aliases/data/aliases.json` (не коммитится).

JS читает алиасы через встроенный маршрут A1111 `file=` (как tag-autocomplete), **без** отдельных FastAPI-эндпоинтов.

## Troubleshooting

- Импорт читает [`/api/prompt-macros`](../../app/api/prompt_macros.py) — те же `@alias`, что на странице `/macros`.
- Редактирование во вкладке SD **не** пишет обратно в web-chat; для master-копии используйте `/macros` и повторный Import.

## Troubleshooting

| Симптом | Решение |
|---------|---------|
| Import: HTTP 401 | Проверить trusted internal: SD IP в `SD_WEBUI_URL` / `TRUSTED_INTERNAL_IPS` |
| Import: connection refused | URL web-chat, firewall LAN |
| Autocomplete пустой | Import или добавить алиас вручную; F12 → `[character-aliases]` |
| `@alias` не разворачивается при Generate | Settings → Expand @alias enabled; проверить `aliases.json` |
| `RuntimeError: Cannot add middleware after an application has started` | Известная гонка ReForge с флагом `--api`; UI работает, REST `/sdapi/v1/` может не подняться. Не связано с character-aliases (расширение не регистрирует FastAPI) |

## ReForge

- `OptionInfo(..., section=...)` — без `.section()` chaining
- `onUiUpdate` для переподключения autocomplete после Reload UI
