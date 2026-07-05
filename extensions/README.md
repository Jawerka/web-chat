# Browser & SD extensions

Внешние расширения для web-chat — **не** часть Python-процесса на `:8090`. Устанавливаются отдельно на рабочую станцию оператора или на хост SD WebUI.

| Каталог | Где ставится | Назначение |
|---------|--------------|------------|
| [`booru-web-chat/`](booru-web-chat/) | Chrome (Load unpacked) | Post-страницы booru/reddit → теги + картинка в **новый чат** web-chat |
| [`sd-webui-web-chat-bridge/`](sd-webui-web-chat-bridge/) | SD WebUI `extensions/` на `.52` | Галерея web-chat → **img2img** в SD (очередь push) |
| [`sd-webui-character-aliases/`](sd-webui-character-aliases/) | SD WebUI `extensions/` на `.52` | `@alias` в positive prompt: import из web-chat, autocomplete, подстановка тегов |

## Связь с API web-chat

| Сценарий | Endpoint web-chat | Клиент |
|----------|-------------------|--------|
| Booru → новый чат | `POST /api/conversations/from-image` | [`booru-web-chat`](booru-web-chat/API.md) |
| Галерея → SD img2img | `POST /api/sd-bridge/import` | [`gallery-sd-bridge.js`](../static/js/gallery-sd-bridge.js) + SD extension |
| SD → @alias import | `GET /api/prompt-macros` | [`sd-webui-character-aliases`](sd-webui-character-aliases/README.md) |

Подробнее:

- [`docs/API-FROM-IMAGE.md`](../docs/API-FROM-IMAGE.md) — контракт `from-image` (JSON + multipart)
- [`booru-web-chat/API.md`](booru-web-chat/API.md) — вызов из Chrome MV3 (cookie, tab-fetch, multipart)
- [`sd-webui-web-chat-bridge/README.md`](sd-webui-web-chat-bridge/README.md) — установка на SD, `/web-chat-bridge/push`

## Быстрый старт

**Booru → web-chat**

```bash
cd extensions/booru-web-chat && npm install && npm run prepare
# Chrome → Extensions → Load unpacked → эта папка
# Войти в web-chat в том же браузере; Options → base URL
```

**Gallery → SD**

```bash
# на хосте SD (.52), в каталог extensions WebUI:
cp -r /path/to/web-chat/extensions/sd-webui-web-chat-bridge .
# Restart WebUI; Settings → Web-Chat Bridge → URL web-chat
```

**Character Aliases (@alias) → SD prompt**

```bash
ln -s /path/to/web-chat/extensions/sd-webui-character-aliases sd-webui-character-aliases
# Restart WebUI → вкладка Character Aliases → Import from web-chat
```
