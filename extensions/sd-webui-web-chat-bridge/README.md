# sd-webui-web-chat-bridge

Расширение для **Stable Diffusion WebUI** (A1111 / Forge / ReForge): one-click импорт изображения и параметров генерации из **web-chat** во вкладку **img2img**.

Галерея web-chat открывает SD с query-параметром `?web_chat_import=TOKEN`. Расширение на хосте SD server-side запрашивает PNG + infotext у web-chat и вызывает нативный paste pipeline A1111 (`Send to img2img`).

## Требования

- SD WebUI с API (обычный Forge/ReForge на `:7860`)
- web-chat с endpoint `GET /api/sd-bridge/import/{token}` (см. основной проект)
- Сеть LAN: SD-хост (например `192.168.88.52`) должен достучаться до web-chat (`192.168.88.44:8090`)

## Установка

### Вариант A — копия из репозитория web-chat

```bash
# на хосте SD (.52)
cd /path/to/stable-diffusion-webui/extensions
cp -r /path/to/web-chat/deploy/sd-webui-web-chat-bridge .
# или symlink:
ln -s /path/to/web-chat/deploy/sd-webui-web-chat-bridge sd-webui-web-chat-bridge
```

### Вариант B — Install from URL

В SD WebUI → **Extensions** → **Install from URL** — URL git-репозитория (подпапка `deploy/sd-webui-web-chat-bridge` нужно клонировать вручную или опубликовать отдельным repo).

После установки: **Apply and restart UI**.

## Настройка

| Способ | Параметр | Пример |
|--------|----------|--------|
| Settings → Web-Chat Bridge | Web-Chat base URL | `http://192.168.88.44:8090` |
| CLI (`webui-user.sh`) | `--web-chat-url` | `--web-chat-url http://192.168.88.44:8090` |

Приоритет: **Settings UI** → **CLI** → default `http://192.168.88.44:8090`.

## Как это работает

1. Пользователь в галереи web-chat нажимает «Отправить в SD WebUI».
2. web-chat server-side POST `{image_base64, infotext, filename}` на SD  
   `POST http://192.168.88.52:7860/web-chat-bridge/push`
3. Расширение кладёт импорт в очередь (TTL ~1 ч).
4. Когда вы открываете SD и вкладку **img2img**, long-poll `/web-chat-bridge/wait-pending` сразу будит apply (без опроса каждые 3 с).
5. PIL хранится server-side (`gr.State`) — PNG не гоняется через браузер дважды.

Новая вкладка браузера **не открывается**.

## API web-chat (контракт)

Расширение ожидает:

```http
GET /api/sd-bridge/import/{token}
Accept: application/json
```

Ответ `200`:

```json
{
  "image_base64": "<PNG bytes, base64>",
  "infotext": "prompt\nNegative prompt: ...\nSteps: 22, Sampler: ...",
  "filename": "generation.png",
  "mime": "image/png"
}
```

Token одноразовый, TTL ~60 с. Запрос идёт **с SD-хоста**, не из браузера (обход CORS).

Создание token — `POST /api/sd-bridge/import` из web-chat (сессия пользователя); см. план в репозитории web-chat.

## Где искать UI в SD WebUI

Аккордеон **«Web-Chat Bridge (gallery import)»** — на вкладке **img2img**, в блоке настроек генерации (always-on script, **не** в выпадающем списке Script).

После импорта prompt, параметры и init image заполняются на этой же вкладке.

## Диагностика

```bash
# с хоста SD
curl -sS "http://127.0.0.1:7860/web-chat-bridge/ping"
# → {"ok": true, "web_chat_url": "http://192.168.88.44:8090"}

curl -sS "http://192.168.88.44:8090/api/sd-bridge/import/TEST_TOKEN"
```

Ручной тест UI: открыть  
`http://192.168.88.52:7860/?web_chat_import=VALID_TOKEN`

Accordion **Web-Chat Bridge** на вкладке img2img показывает статус последнего импорта.

## Структура

```
sd-webui-web-chat-bridge/
  preload.py              # --web-chat-url CLI default
  scripts/
    web_chat_bridge.py    # fetch + paste binding + /web-chat-bridge/ping
  javascript/
    web_chat_bridge.js    # ?web_chat_import= auto-start
  README.md
```

## Совместимость

- **A1111** — `modules.infotext_utils.register_paste_params_button`
- **Forge / ReForge** — тот же extension API, без fork-specific кода
- Не использует headless `/sdapi/v1/img2img` (только заполнение UI)

## Troubleshooting

| Симптом | Решение |
|---------|---------|
| SD открылся, img2img пустой | Extension не обновлён / UI не в DOM — см. «Где искать UI»; F12 → `[web-chat-bridge]` |
| Аккордеона нет | Старый скрипт был в dropdown Script — обновите `web_chat_bridge.py` (`AlwaysVisible`) |
| `Cannot reach web-chat` | Проверить URL в Settings, ping с `.52` до `.44:8090` |
| HTTP 401/403 на import | Token истёк или уже использован; IP SD не в trusted на web-chat |
| HTTP 404 на import | web-chat ещё без `/api/sd-bridge` — обновить web-chat |
| `TypeError: 'NoneType' object is not callable` в ui_settings | Обновите `web_chat_bridge.py`: `section=` в конструкторе `OptionInfo`, не `opt.section(...)` |
