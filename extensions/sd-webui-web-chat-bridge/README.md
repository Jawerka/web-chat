# sd-webui-web-chat-bridge

Расширение для **Stable Diffusion WebUI** (A1111 / Forge / ReForge): импорт PNG + A1111 infotext из **галереи web-chat** во вкладку **img2img**.

Расположение в репозитории: **`extensions/sd-webui-web-chat-bridge/`** (ранее `deploy/sd-webui-web-chat-bridge/`).

## Требования

- SD WebUI с API (Forge/ReForge на `:7860`)
- web-chat с `POST /api/sd-bridge/import` и push на SD
- LAN: SD-хост (например `192.168.88.52`) достучится до web-chat (`192.168.88.44:8090`)

## Установка

```bash
# на хосте SD (.52)
cd /path/to/stable-diffusion-webui/extensions
cp -r /path/to/web-chat/extensions/sd-webui-web-chat-bridge .
# или symlink:
ln -s /path/to/web-chat/extensions/sd-webui-web-chat-bridge sd-webui-web-chat-bridge
```

После копирования: **Apply and restart UI** в SD WebUI.

## Настройка

| Способ | Параметр | Пример |
|--------|----------|--------|
| Settings → Web-Chat Bridge | Web-Chat base URL | `http://192.168.88.44:8090` |
| CLI (`webui-user.sh`) | `--web-chat-url` | `--web-chat-url http://192.168.88.44:8090` |

Приоритет: **Settings UI** → **CLI** → default `http://192.168.88.44:8090`.

## Как это работает (основной путь)

1. В lightbox галереи / чата web-chat — **«Отправить в SD WebUI»** ([`gallery-sd-bridge.js`](../../static/js/gallery-sd-bridge.js)).
2. Браузер → `POST /api/sd-bridge/import` (сессия web-chat) с `asset_id` + `source`.
3. **web-chat server-side** загружает PNG + infotext и шлёт на SD:  
   `POST http://192.168.88.52:7860/web-chat-bridge/push`
4. Расширение SD кладёт импорт в очередь (TTL ~1 ч).
5. На вкладке **img2img** long-poll `GET /web-chat-bridge/wait-pending` → apply → paste в img2img.

**Новая вкладка браузера не открывается.**

### Legacy (опционально)

Query `?web_chat_import=TOKEN` и `GET /api/sd-bridge/import/{token}` — одноразовый token (~60 с), fetch с SD-хоста. Основной UX галереи использует **push-очередь**, не token URL.

## API web-chat

### Создание импорта (из UI галереи)

```http
POST /api/sd-bridge/import
Content-Type: application/json
Cookie: webchat_session=...

{
  "asset_id": "uuid-or-filename",
  "source": "db",
  "sd_webui_url": "http://192.168.88.52:7860"
}
```

Ответ `200`:

```json
{
  "queued": true,
  "filename": "generation.png",
  "sd_webui_url": "http://192.168.88.52:7860"
}
```

Код: [`app/api/sd_bridge.py`](../../app/api/sd_bridge.py), [`app/services/sd_bridge_service.py`](../../app/services/sd_bridge_service.py).

### Legacy fetch (SD extension, token)

```http
GET /api/sd-bridge/import/{token}
```

Без cookie — token HMAC; IP SD должен быть доверенным для media. Ответ: `image_base64`, `infotext`, `filename`, `mime`.

## API на SD WebUI (это расширение)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/web-chat-bridge/ping` | health + URL web-chat |
| POST | `/web-chat-bridge/push` | приём очереди (только LAN) |
| GET | `/web-chat-bridge/pending` | есть ли импорт в очереди |
| GET | `/web-chat-bridge/wait-pending` | long-poll до импорта |

## UI в SD WebUI

Аккордеон **«Web-Chat Bridge (gallery import)»** — вкладка **img2img**, always-visible script (не dropdown Script).

## Диагностика

```bash
# с хоста SD
curl -sS "http://127.0.0.1:7860/web-chat-bridge/ping"

# очередь пуста после успешного apply
curl -sS "http://127.0.0.1:7860/web-chat-bridge/pending"
```

F12 в SD → фильтр `[web-chat-bridge]`.

## Структура

```text
extensions/sd-webui-web-chat-bridge/
  preload.py
  scripts/web_chat_bridge.py    # queue, push, paste, FastAPI routes
  javascript/web_chat_bridge.js # long-poll, apply, legacy ?web_chat_import=
  README.md
```

## Troubleshooting

| Симптом | Решение |
|---------|---------|
| img2img пустой | Обновить extension; открыта вкладка img2img; F12 `[web-chat-bridge]` |
| Аккордеона нет | `AlwaysVisible` в `web_chat_bridge.py`; перезапуск WebUI |
| `Cannot reach web-chat` | URL в Settings; curl с `.52` до `.44:8090` |
| HTTP 502 на import | SD `/web-chat-bridge/push` недоступен или отклонил push |
| HTTP 403 push | push только с private/LAN IP |
| ReForge startup error | `OptionInfo(..., section=...)` — не `.section()` chain |

См. также [`docs/RUNBOOK.md`](../../docs/RUNBOOK.md) — раздел «Gallery → SD WebUI».
