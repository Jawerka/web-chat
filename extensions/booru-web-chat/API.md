# API — Booru → web-chat

Документ для самостоятельной разработки расширения. Описывает контракт **web-chat backend**, способ вызова из **Chrome MV3 service worker** и внутренние точки расширения.

Исходники backend (не менять без необходимости):

| Файл | Назначение |
|------|------------|
| [`app/api/conversation_import.py`](../../app/api/conversation_import.py) | HTTP handler |
| [`app/services/conversation_import_service.py`](../../app/services/conversation_import_service.py) | бизнес-логика |
| [`app/api/schemas.py`](../../app/api/schemas.py) | Pydantic-модели |
| [`static/js/gallery-common.js`](../../static/js/gallery-common.js) | эталон вызова из UI галереи |

---

## Общий поток (текущая реализация v1.0.2)

```text
Клик по иконке расширения
  │
  ├─ inject.js на вкладке booru
  │    extractPage() → tags, imageUrl, text, pageUrl
  │    clipboard ← text (отсортированные теги через ", ")
  │
  ├─ executeScript на той же вкладке
  │    fetch(imageUrl) → base64 + mime   ← Referer = страница поста
  │
  ├─ background.js
  │    Cookie: webchat_session (chrome.cookies)
  │    POST multipart → /api/conversations/from-image
  │
  └─ chrome.tabs.create(baseUrl + chat_url)
```

**Почему не JSON с `image.url`:** CDN booru/reddit часто отдаёт **403** без Referer со страницы поста. Сервер web-chat и service worker расширения не имеют этого Referer. Загрузка только из **tab context** booru.

**Почему не `credentials: 'include'`:** cookie `webchat_session` имеет `SameSite=Lax` и **не отправляется** на cross-site POST из origin `chrome-extension://`. Используйте `chrome.cookies.get` + заголовок `Cookie` (см. ниже).

---

## Endpoint web-chat

### `POST /api/conversations/from-image`

Создаёт **новую беседу** с одним вложением-изображением и текстом для **composer** (черновик, сообщение пользователя **не отправляется** агенту).

| | |
|---|---|
| **URL** | `{WEB_CHAT_BASE}/api/conversations/from-image` |
| **Auth** | сессия `webchat_session` (если `AUTH_ENABLED=true`) |
| **Success** | `201 Created` |
| **Content-Type (расширение)** | `multipart/form-data` |

Эталон в галерее: `GalleryCommon.attachImageToNewChat()` в [`gallery-common.js`](../../static/js/gallery-common.js) — там JSON с `asset_id` / `disk_filename`; расширение использует **multipart с файлом**.

---

### Multipart (используется расширением)

Поля формы:

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `image` | file | да | PNG/JPEG/WebP/GIF, лимит см. ниже |
| `text` | string | нет | Текст composer; расширение шлёт отсортированные теги |
| `title` | string | нет | Заголовок беседы; расширение: `"Новая беседа"` |
| `preset_slug` | string | нет | Пресет; расширение: `"img2img"` (из Options) |

Пример `curl` (замените cookie и путь к файлу):

```bash
curl -sS -X POST "http://192.168.88.44:8090/api/conversations/from-image" \
  -H "Cookie: webchat_session=PASTE_TOKEN_HERE" \
  -F "text=anthro, dragon, solo" \
  -F "title=Новая беседа" \
  -F "preset_slug=img2img" \
  -F "image=@/tmp/post.jpg;type=image/jpeg"
```

Эквивалент в JS (service worker расширения):

```javascript
const form = new FormData();
form.append("text", "anthro, dragon, solo");
form.append("title", "Новая беседа");
form.append("preset_slug", "img2img");
form.append("image", blob, "post.jpg");

const cookie = await chrome.cookies.get({
  url: "http://192.168.88.44:8090/",
  name: "webchat_session",
});

const res = await fetch("http://192.168.88.44:8090/api/conversations/from-image", {
  method: "POST",
  headers: { Cookie: `webchat_session=${cookie.value}` },
  body: form,
});
```

**Важно:** для `FormData` **не** задавайте `Content-Type` вручную — браузер добавит boundary.

---

### JSON (альтернатива, сейчас не используется расширением)

Поддерживается backend для галереи и внешних URL без hotlink-защиты.

```http
POST /api/conversations/from-image
Content-Type: application/json
Cookie: webchat_session=...

{
  "title": "Новая беседа",
  "text": "tag1, tag2, tag3",
  "preset_slug": "img2img",
  "image": {
    "url": "https://example.com/image.jpg"
  }
}
```

В `image` **ровно одно** поле:

| Поле | Описание |
|------|----------|
| `asset_id` | UUID медиа из галереи web-chat |
| `disk_filename` | файл из `data/generated/` |
| `url` | HTTP(S) URL; сервер скачивает сам ([`_fetch_url_bytes`](../../app/services/media_service.py)) |

Можно вернуть JSON-путь для сайтов без 403, но для e621/rule34/derpibooru надёжнее multipart + tab fetch.

---

### Ответ `201`

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Новая беседа",
  "preset_id": "…",
  "composer_text": "tag1, tag2, tag3",
  "chat_url": "/?conv=550e8400-e29b-41d4-a716-446655440000",
  "attachments": [
    {
      "id": "…",
      "original_name": "post.jpg",
      "mime_type": "image/jpeg",
      "size_bytes": 123456,
      "preview_url": "/media/asset/…/preview"
    }
  ]
}
```

| Поле | Использование в расширении |
|------|------------------------------|
| `chat_url` | открыть `{webChatBaseUrl}{chat_url}` |
| `conversation_id` | fallback: `/?conv={id}` |
| `composer_text` | должен совпадать с отправленным `text` |
| `attachments` | вложение уже привязано к беседе; UI подхватит при открытии чата |

После создания **нет** user-сообщения в БД — только `composer_draft_text` и attachment без `message_id` (см. тесты в [`tests/test_conversation_import.py`](../../tests/test_conversation_import.py)).

---

### Ошибки

| HTTP | `detail` (примеры) | Причина |
|------|-------------------|---------|
| `401` | `Требуется вход`, `code: auth_required` | нет / протухла cookie |
| `400` | `Не указан источник изображения` | пустой multipart |
| `400` | `Не удалось загрузить изображение: …` | JSON `image.url` — fetch с сервера упал |
| `404` | `MediaAsset не найден` | неверный `asset_id` |
| `415` | `Только изображения` | не image MIME |
| `400` | `Изображение слишком большое` | > 15 MB ([`_MAX_IMPORT_BYTES`](../../app/services/media_service.py)) |

Тело ошибки FastAPI:

```json
{ "detail": "строка или массив validation errors" }
```

---

## Аутентификация из расширения

### Cookie

| | |
|---|---|
| **Имя** | `webchat_session` |
| **Флаги** | `HttpOnly`, `SameSite=Lax` |
| **Получение** | `POST /api/auth/login` или вход через `/login` |
| **Проверка** | `GET /api/auth/me` |

### Manifest permissions

```json
"permissions": ["cookies", "storage", "scripting", "activeTab"]
```

```json
"host_permissions": [
  "http://192.168.88.44:8090/*",
  "*://*.e621.net/*",
  ...
]
```

При смене base URL в Options вызывается `chrome.permissions.request({ origins: ["http://host:port/*"] })`.

### Реализация в `background.js`

```javascript
const SESSION_COOKIE_NAME = "webchat_session";

async function getSessionCookieValue(baseUrl) {
  const origin = new URL(`${baseUrl}/`).origin;
  const cookie = await chrome.cookies.get({
    url: `${origin}/`,
    name: SESSION_COOKIE_NAME,
  });
  if (!cookie?.value) throw new Error("Not logged in to web-chat …");
  return cookie.value;
}

async function webChatFetch(baseUrl, path, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("Cookie", `${SESSION_COOKIE_NAME}=${await getSessionCookieValue(baseUrl)}`);
  return fetch(`${baseUrl}${path}`, { ...init, headers });
}
```

При `AUTH_ENABLED=false` на dev-стенде cookie не требуется — middleware пропускает запросы.

---

## Загрузка изображения с booru (tab context)

Выполняется **на вкладке поста**, не в service worker:

```javascript
await chrome.scripting.executeScript({
  target: { tabId },
  func: async (url) => {
    const res = await fetch(url);
    if (!res.ok) return { ok: false, status: res.status };
    const blob = await res.blob();
    // … base64 для передачи в background
  },
  args: [imageUrl],
});
```

Браузер сам ставит `Referer: https://e621.net/posts/…` (или rule34 / derpibooru).

Ограничение: результат `executeScript` сериализуется — очень большие файлы (> ~10–15 MB) могут быть медленными; backend режет импорт на **15 MB**.

---

## Настройки расширения (`chrome.storage.sync`)

| Ключ | Default | Описание |
|------|---------|----------|
| `webChatBaseUrl` | `http://192.168.88.44:8090` | без trailing `/` |
| `presetSlug` | `img2img` | поле `preset_slug` в API |

Чтение: `getSettings()` в [`src/background.js`](src/background.js). UI: [`src/options.html`](src/options.html).

---

## Внутренний API расширения (content script)

После инжекта [`dist/inject.js`](dist/inject.js) (сборка из [`src/inject.js`](src/inject.js)):

```javascript
globalThis.__booruWebChat.extractPage()
```

### `extractPage()` → результат

**Успех:**

```javascript
{
  ok: true,
  count: 42,              // число тегов
  text: "a, b, c",        // formatTags(tags) — в clipboard и в API text
  tags: ["a", "b", "c"],  // сырой массив
  imageUrl: "https://…",  // абсолютный URL
  pageUrl: "https://e621.net/posts/123"
}
```

**Ошибка:**

```javascript
{ ok: false, error: "No tags found on this page" }
{ ok: false, error: "No image found on this page" }
```

### Extractors

| Модуль | Теги | Картинка |
|--------|------|----------|
| [`src/extractors/e621.js`](src/extractors/e621.js) | `#tag-list .tag-list-item[data-name]` | см. [`images.js`](src/extractors/images.js) |
| [`src/extractors/rule34.js`](src/extractors/rule34.js) | `#tag-sidebar li.tag a` | `#image` src |
| [`src/extractors/reddit.js`](src/extractors/reddit.js) | post title, `r/subreddit`, flair | `shreddit-post` / `content-href`, srcset |
| [`src/extractors/derpibooru.js`](src/extractors/derpibooru.js) | hidden `#tags-form_old_tag_input` или `.tag[data-tag-name]` | `#image-display`; `/medium.` → `/full.` |
| [`src/extractors/generic.js`](src/extractors/generic.js) | fallback | `#image` / `picture img` |

Формат тегов: [`src/format.js`](src/format.js) — dedupe, sort `localeCompare`, join `", "`.

Добавление сайта:

1. `src/extractors/news site.js` — `extractTags(doc)`, при необходимости image helper;
2. зарегистрировать в [`src/extractors/index.js`](src/extractors/index.js) и [`src/extractors/images.js`](src/extractors/images.js);
3. `host_permissions` в [`manifest.json`](manifest.json);
4. тест с HTML-снимком в [`tests/`](tests/) (ref можно положить рядом или в `tag-copy/ref/`).

---

## Открытие чата после успеха

```javascript
const chatPath = data.chat_url || `/?conv=${data.conversation_id}`;
const chatUrl = chatPath.startsWith("http")
  ? chatPath
  : `${settings.webChatBaseUrl}${chatPath.startsWith("/") ? chatPath : `/${chatPath}`}`;

await chrome.tabs.create({ url: chatUrl });
```

UI web-chat при загрузке читает `composer_draft_text` и pending attachments (см. [`static/js/gallery-common.js`](../../static/js/gallery-common.js) — `WebChatComposer.primeComposerDraft` / `sessionStorage`).

---

## Связанные endpoint (не вызываются расширением)

| Endpoint | Зачем знать |
|----------|-------------|
| `POST /api/auth/login` | получить cookie вручную для curl |
| `GET /api/auth/me` | проверить сессию |
| `GET /api/config` | публичные лимиты, URLs |
| `POST /api/sd-bridge/import` | другой сценарий (галерея → SD WebUI), **не** этот плагин |

---

## Локальная проверка без расширения

```bash
# 1. Войти в web-chat в браузере, скопировать cookie webchat_session из DevTools

# 2. Multipart — как расширение
curl -sS -w "\nHTTP %{http_code}\n" \
  -X POST "http://127.0.0.1:8090/api/conversations/from-image" \
  -H "Cookie: webchat_session=TOKEN" \
  -F "text=anthro, solo" \
  -F "preset_slug=img2img" \
  -F "image=@test.jpg"

# 3. Backend-тест контракта (mock URL fetch)
cd /root/web-chat && source .venv/bin/activate
pytest tests/test_conversation_import_external_url.py -q
```

---

## Сборка и тесты расширения

```bash
cd extensions/booru-web-chat
npm install
npm run build      # dist/inject.js
npm test           # vitest, ref HTML: ../../tag-copy/ref/
```

После правок `src/inject.js` или extractors — **обязательно** `npm run build` и Reload в `chrome://extensions`.

---

## Чеклист при доработке

- [ ] Cookie читается через `chrome.cookies`, не `credentials: 'include'`
- [ ] Изображение booru качается в **tab context**, не из service worker
- [ ] Multipart: не задавать `Content-Type` вручную
- [ ] `text` ≤ 100 000 символов (лимит schema)
- [ ] `image.url` в JSON — только если уверены, что CDN не требует Referer
- [ ] Новый `webChatBaseUrl` → `host_permissions` + Options
- [ ] pytest web-chat не ломать: `tests/test_conversation_import*.py`

---

## Версия документа

Соответствует расширению **v1.0.2** (`manifest.json`). При смене контракта обновите этот файл и секцию API в [`README.md`](README.md).
