# Booru → web-chat

Chrome-расширение (MV3): на post-странице booru копирует теги в буфер и создаёт новую беседу в [web-chat](../../) с изображением поста и тегами в поле composer.

Поддерживаемые сайты: **e621**, **e926**, **rule34.xxx**, **derpibooru**, **reddit.com**.

## Что делает кнопка

1. Извлекает теги (как [tag-copy](../../tag-copy/)) → копирует в буфер обмена.
2. Берёт главное изображение поста (`#image`, `#image-display`, og:image и т.д.).
3. Скачивает изображение **в контексте вкладки booru** (Referer/cookies CDN) и шлёт multipart на `POST /api/conversations/from-image`.
4. Открывает новый чат (`/?conv=…`) с пресетом `img2img`.

Backend **не меняется** — используется тот же endpoint, что и кнопка «В новый чат» в галерее ([`gallery-common.js`](../../static/js/gallery-common.js)).

**Подробная документация API:** [`API.md`](API.md) — контракт web-chat, auth, multipart, extractors, curl, чеклист доработки.

## Установка

```bash
cd extensions/booru-web-chat
npm install
npm run prepare   # icons + dist/inject.js
```

В Chrome: **Extensions → Load unpacked** → выберите папку `extensions/booru-web-chat`.

## Настройка

1. ПКМ по иконке → **Options**.
2. Укажите **web-chat base URL** (по умолчанию `http://192.168.88.44:8090`).
3. **preset slug** — обычно `img2img`.

## Авторизация

На стенде с `AUTH_ENABLED=true` нужно **войти в web-chat в том же браузере** до использования расширения. Cookie `webchat_session` (SameSite=Lax) не отправляется автоматически из service worker расширения — расширение читает её через `chrome.cookies` и передаёт в запросе.

## API

Кратко: multipart `POST /api/conversations/from-image` + cookie `webchat_session` через `chrome.cookies`.

Полное описание — **[API.md](API.md)** (форматы запроса/ответа, ошибки, tab-fetch, внутренние extractors, curl, чеклист).

## Разработка

```bash
npm test          # vitest (ref HTML из tag-copy/ref)
npm run build     # пересобрать dist/inject.js
```

## Troubleshooting

| Симптом | Что проверить |
|---------|----------------|
| Badge `!` | Страница не post-view или нет тегов/картинки |
| HTTP 401 / Not logged in | Войдите в web-chat; перезагрузите расширение после обновления |
| HTTP 403 на image | Обновите расширение до 1.0.2+ (скачивание в tab context) |
| Новая вкладка не открывается | Host permission для URL web-chat в Options |
