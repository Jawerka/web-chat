# API: изображение + текст → новый чат

Инструкция для **внешних клиентов**, **скриптов в LAN** и **ИИ-агентов**, которые хотят передать картинку и сопровождающий текст в web-chat.

---

## Назначение

**`POST /api/conversations/from-image`** создаёт **новую беседу** и подготавливает **черновик** в окне ввода:

| Что делает API | Что **не** делает |
|----------------|-------------------|
| Создаёт беседу с выбранным пресетом | Не отправляет сообщение агенту |
| Прикрепляет **одно** изображение к composer (полоса вложений) | Не запускает LLM / SD |
| Возвращает текст для поля ввода (`composer_text`) | Не стримит ответ в WebSocket |

Пользователь (или следующий шаг вашего клиента) **сам нажимает «Отправить»** в UI — либо вы вызываете отдельно `POST /api/conversations/{id}/turn`.

Типичные сценарии:

- кнопка **«В чат»** в галерее web-chat;
- Chrome-расширение **booru-web-chat** (e621, rule34, derpibooru, reddit) — см. [§ Chrome extension](#chrome-extension-booru--web-chat);
- скрипт на другой машине в LAN, который шлёт файл + prompt;
- интеграция с внешней галереей по HTTP.

---

## Адрес и транспорт

| Параметр | Значение |
|----------|----------|
| Метод | `POST` |
| Путь | `/api/conversations/from-image` |
| Полный URL (LAN) | `http://192.168.88.44:8090/api/conversations/from-image` |
| Порт | тот же, что у UI (`WEB_PORT`, по умолчанию **8090**) |
| Ответ при успехе | **201 Created**, JSON |

Сервер слушает `0.0.0.0` — отдельный порт или микросервис **не нужны**.

---

## Аутентификация

На стенде с `AUTH_ENABLED=true` (тиично для production):

- Запросы из **браузера** (галерея, тот же origin) — cookie сессии `webchat_session` передаётся автоматически.
- Запросы из **curl/скрипта** — передайте cookie после `POST /api/auth/login` или используйте сессию из браузера (`-b cookies.txt`).
- **Chrome extension** [`extensions/booru-web-chat/`](../extensions/booru-web-chat/) — cookie `webchat_session` читается через `chrome.cookies` и передаётся заголовком `Cookie` (не `credentials: 'include'`: SameSite=Lax блокирует POST из `chrome-extension://`). Подробно: [`extensions/booru-web-chat/API.md`](../extensions/booru-web-chat/API.md).

Без сессии: **401** `{"detail":"Требуется вход","code":"auth_required"}`.

**Логин для внешних клиентов (curl, Reembow Gallery):**

```http
POST /api/auth/login
Content-Type: application/json

{"login": "admin", "password": "..."}
```

Поле строго **`login`**, не `username` — иначе **422** `Field required` для `body.login`.

Для LAN без auth (`AUTH_ENABLED=false`) endpoint доступен без cookie.

---

## Два формата запроса

Выберите **один** формат. Content-Type определяет разбор тела.

### 1. JSON (`application/json`)

Когда изображение **уже есть** в web-chat (галерея, `MediaAsset`) или доступно по внутреннему URL.

### 2. Multipart (`multipart/form-data`)

Когда клиент передаёт **сырые байты** файла (внешний скрипт, другое приложение).

---

## JSON: схема запроса

```json
{
  "text": "строка для поля ввода чата",
  "title": "Новая беседа",
  "preset_slug": "img2img",
  "image": {
    "asset_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

### Поля верхнего уровня

| Поле | Тип | Обязательно | По умолчанию | Описание |
|------|-----|-------------|--------------|----------|
| `text` | string | нет | `""` | Текст в composer (prompt, комментарий, инструкция). До 100 000 символов. |
| `title` | string | нет | `"Новая беседа"` | Заголовок беседы в сайдбаре. До 200 символов. |
| `preset_slug` | string | нет | `"img2img"` | Пресет агента (см. таблицу ниже). |
| `image` | object | **да** | — | Источник картинки (ровно одно поле внутри). |

### Пресеты (`preset_slug`)

| slug | Назначение |
|------|------------|
| `img2img` | Перерисовка / доработка по вложению (**рекомендуется** для «картинка из галереи → чат») |
| `image_gen` | Генерация с нуля (txt2img) |
| `default` | Универсальный ассистент |
| `document_analysis` | Анализ документов |

Если slug не найден → **404** `"Пресет не найден"`.

### Объект `image` — ровно одно поле

Укажите **только один** источник. Несколько полей сразу → **400**.

#### Вариант A: `asset_id` (предпочтительно для галереи)

```json
"image": { "asset_id": "uuid-media-asset" }
```

- Картинка уже в БД (`/media/asset/{uuid}`).
- **Байты не копируются** — создаётся только ссылка `Attachment` → существующий `MediaAsset`.
- Быстро и без дублирования в БД.
- `asset_id` берите из `GET /api/gallery` или `GET /api/gallery/uploads` (поле `id` у элементов с `source: "db"`).

#### Вариант B: `disk_filename`

```json
"image": { "disk_filename": "2024-01-15_123456.png" }
```

- Файл в `data/generated/` на сервере (legacy, ещё не ingested в БД).
- Байты читаются с диска и сохраняются в БД как новый `MediaAsset` для беседы.

#### Вариант C: `url`

```json
"image": { "url": "/media/asset/550e8400-e29b-41d4-a716-446655440000" }
```

или

```json
"image": { "url": "/media/generated/foo.png" }
```

или абсолютный URL в доверенной LAN-сети.

Поведение:

- `/media/asset/{uuid}` → как `asset_id` (без копирования, если asset существует).
- `/media/generated/{file}` → как `disk_filename`.
- Другие URL → сервер **скачивает** изображение и создаёт новый asset (квота upload).

---

## Multipart: поля формы

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `image` | file | **да** | Файл изображения |
| `text` | string | нет | Текст для composer |
| `title` | string | нет | Заголовок беседы |
| `preset_slug` | string | нет | По умолчанию `img2img` |

Поддерживаемые типы: `image/jpeg`, `image/png`, `image/webp`, `image/gif`.  
Лимит размера: `MAX_UPLOAD_MB` (по умолчанию **25** МБ).

Пример:

```bash
curl -sS -X POST "http://192.168.88.44:8090/api/conversations/from-image" \
  -b /path/to/cookies.txt \
  -F "text=Опиши эту сцену подробно" \
  -F "title=Импорт из скрипта" \
  -F "preset_slug=default" \
  -F "image=@/path/to/photo.png;type=image/png"
```

---

## Ответ `201 Created`

```json
{
  "conversation_id": "c809bb4f-27dc-47aa-8d20-d1cf45daf3d5",
  "title": "Новая беседа",
  "preset_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "attachments": [
    {
      "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      "original_name": "gal.png",
      "mime_type": "image/png",
      "size_bytes": 12345,
      "preview_url": "/media/asset/550e8400-e29b-41d4-a716-446655440000/thumb"
    }
  ],
  "composer_text": "опиши картинку",
  "chat_url": "/?conv=c809bb4f-27dc-47aa-8d20-d1cf45daf3d5"
}
```

| Поле | Назначение |
|------|------------|
| `conversation_id` | UUID новой беседы |
| `attachments[0].id` | UUID вложения для последующего `turn` / WS `user_message` |
| `composer_text` | Эхо переданного `text` (после trim) |
| `chat_url` | Относительный URL для открытия чата в браузере |

В БД **нет** user-сообщения (`messages` пусто) — только беседа + pending attachment (`message_id = null`).  
Текст черновика сохраняется в `conversations.composer_draft_text` для подгрузки UI.

---

## Handoff: внешняя галерея (Reembow) → браузер

Сценарий: API вызывается **с сервера галереи** (другая машина), браузер оператора только открывает `chat_url`.

| Слой | Поведение |
|------|-----------|
| API-сессия галереи | Cookie `webchat_session` на сервере `.33` |
| Браузер оператора | **Отдельная** cookie — нужен login в UI web-chat под **тем же** пользователем (`admin`) |
| Черновик в UI | `GET /api/conversations/{id}` → `composer_text` + `pending_attachments[]` (только если `message_count == 0`) |

**Важно:** `pending_attachments` и `composer_text` в GET возвращаются **только для пустых бесед** (ещё нет сообщений в истории). После первого «Отправить» handoff считается потреблённым; неотправленные pending-вложения удаляются при send. Старые сироты в БД: `python -m app.scripts.cleanup_orphan_pending_attachments --dry-run`.

**Рекомендуемый URL:** `http://{PUBLIC_BASE_URL}/?conv={conversation_id}` (из поля `chat_url`).

После открытия UI подгружает серверный черновик — `localStorage` на машине галереи **не нужен**.

### Только теги (видео / без картинки)

```http
POST /api/conversations
Content-Type: application/json

{"title": "Новая беседа", "text": "теги", "preset_slug": "default"}
```

Ответ **201** — те же поля handoff: `conversation_id`, `composer_text`, `chat_url`, `attachments: []`.

### Чеклист для оператора Reembow

- Открывать `chat_url` из ответа API целиком (`WEBCHAT_BASE_URL + chat_url`)
- Передавать теги в поле `text` при `from-image`
- Popup blocker: `window.open('about:blank')` **до** `await fetch`, затем `w.location.href = url`
- Для видео: `POST /api/conversations` с `text`, не пустой `from-image`

---

## Как открыть чат после вызова API

### В браузере (рекомендуется для человека)

1. Вызвать API (из галереи это делает JS автоматически).
2. Перейти по `chat_url` или полному URL:  
   `http://192.168.88.44:8090/?conv={conversation_id}`
3. UI запросит `GET /api/conversations/{id}` и восстановит черновик с сервера: картинка во вложениях, текст в поле ввода.
4. Пользователь редактирует при необходимости и жмёт **Отправить**.

Встроенная галерея web-chat (same-origin) дополнительно пишет черновик в `localStorage` — для внешних клиентов достаточно server draft.

### Программно отправить агенту (без UI)

Если нужен **полностью автоматический** ход после импорта:

```http
POST /api/conversations/{conversation_id}/turn
Content-Type: application/json

{
  "text": "тот же или уточнённый текст",
  "attachment_ids": ["f47ac10b-58cc-4372-a567-0e02b2c3d479"]
}
```

Ответ **202** `{"status":"started","conversation_id":"..."}`.  
Статус: `GET /api/conversations/{id}/generation-status`, история: `GET /api/conversations/{id}/messages`.

**Важно:** `text` в `turn` **не может быть пустым** (минимум 1 символ). Если при импорте `text` был пустым, задайте его в `turn`.

---

## Ошибки

| HTTP | Типичная причина | `detail` (примеры) |
|------|------------------|---------------------|
| 400 | Неверное тело | `Укажите ровно одно поле в image…`, `Не передан файл image` |
| 401 | Нет сессии | `Требуется вход` |
| 403 | Чужой upload-gallery asset | `Нет доступа к изображению` |
| 404 | Asset / файл / пресет не найден | `MediaAsset не найден`, `Файл генерации не найден` |
| 413 | Файл слишком большой | превышен `MAX_UPLOAD_MB` |
| 415 | Не изображение / битый файл | `Тип файла не поддерживается`, ошибки валидации magic bytes |

Тело ошибки FastAPI: `{"detail": "строка или объект"}`.

---

## Chrome extension (booru → web-chat)

Расширение [`extensions/booru-web-chat/`](../extensions/booru-web-chat/) **не использует JSON `image.url`**: CDN booru отдаёт 403 без Referer страницы поста.

| Шаг | Действие |
|-----|----------|
| 1 | На вкладке post (e621, rule34, derpibooru, reddit) — клик по иконке расширения |
| 2 | Теги → clipboard + tab-context `fetch(imageUrl)` |
| 3 | `POST /api/conversations/from-image` **multipart** (`text`, `image` file, `preset_slug`) |
| 4 | `chrome.tabs.create` → `chat_url` |

Полный контракт и чеклист доработки: [`extensions/booru-web-chat/API.md`](../extensions/booru-web-chat/API.md).

---

## Хорошие практики

### Выбор источника изображения

1. **Картинка уже в web-chat** → `image.asset_id` (галерея, чат, uploads).
2. **Файл на диске сервера** (`data/generated/`) → `disk_filename`.
3. **Файл с клиентской машины** → multipart `image=@file`.
4. **`url` в JSON** — только если CDN не требует Referer; booru/reddit → multipart из tab context (см. booru-web-chat).

### Текст (`text`)

- Кладите сюда prompt, вопрос пользователя или SD metadata (prompt / negative / params).
- Пустой `text` допустим при импорте, но **отправка** через WS/turn потребует непустой строки.
- Для img2img имеет смысл передавать теги или описание желаемого результата.

### Пресет

- Изображение для **доработки** → `preset_slug: "img2img"`.
- Нужен **анализ** без генерации → `default` или `document_analysis` (без SD-tools для картинки).

### Идемпотентность

Каждый вызов создаёт **новую** беседу. Повторный клик «В чат» = второй чат. На клиенте:

- блокируйте кнопку на время запроса (`disabled` + статус);
- не ретраить без необходимости — иначе появятся дубликаты бесед.

### Атомарность

Один запрос `from-image` = беседа + вложение в одной транзакции.  
**Не** разбивайте на `POST /conversations` → `GET /media/...` → `POST /upload` — старый трёхшаговый способ медленнее и при сбое оставляет пустые беседы.

### Открытие чата

- Используйте `chat_url` из ответа (`/?conv=...`).
- Полный URL: `{PUBLIC_BASE_URL}{chat_url}` (на стенде `http://192.168.88.44:8090`).

---

## Примеры для ИИ и разработчиков

### Python (JSON, asset из галереи)

```python
import httpx

BASE = "http://192.168.88.44:8090"
cookies = {...}  # после login, или requests.Session с auth

payload = {
    "text": "1girl, sunset, detailed background",
    "title": "Из галереи",
    "preset_slug": "img2img",
    "image": {"asset_id": "550e8400-e29b-41d4-a716-446655440000"},
}

r = httpx.post(f"{BASE}/api/conversations/from-image", json=payload, cookies=cookies)
r.raise_for_status()
data = r.json()
print("Откройте:", BASE + data["chat_url"])
```

### JavaScript (fetch из той же галереи)

```javascript
const res = await fetch('/api/conversations/from-image', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  credentials: 'same-origin',
  body: JSON.stringify({
    text: composerText,
    preset_slug: 'img2img',
    image: { asset_id: galleryItem.id },
  }),
});
const data = await res.json();
window.WebChatComposer?.primeComposerDraft(data.conversation_id, {
  text: data.composer_text,
  attachments: data.attachments,
});
localStorage.setItem('webchat_conv_id', data.conversation_id);
location.href = data.chat_url;
```

### Маппинг элемента галереи web-chat → `image`

| `item.source` | Поле `image` |
|---------------|--------------|
| `"db"` | `{ "asset_id": item.id }` |
| `"disk"` | `{ "disk_filename": item.filename }` |

### Полный цикл: импорт + автоматический turn

```bash
CONV=$(curl -sS -b cookies.txt -X POST "$BASE/api/conversations/from-image" \
  -H "Content-Type: application/json" \
  -d '{"text":"Опиши","image":{"asset_id":"'"$ASSET_ID"'"}}')

CID=$(echo "$CONV" | jq -r .conversation_id)
AID=$(echo "$CONV" | jq -r .attachments[0].id)

curl -sS -b cookies.txt -X POST "$BASE/api/conversations/$CID/turn" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Опиши\",\"attachment_ids\":[\"$AID\"]}"
```

---

## Ограничения (v1)

- Одно изображение за запрос.
- Нет server-side хранения черновика — текст для UI возвращается в ответе; клиент браузера сохраняет в `localStorage`.
- Нет WebSocket push «открой чат» — только redirect / `chat_url`.
- Галерея upload с шифрованием: доступ только владельцу (сессия того же пользователя).

---

## Связанные endpoint'ы

| Endpoint | Когда использовать |
|----------|-------------------|
| `GET /api/gallery` | Список генераций (`id`, `filename`, `source`) |
| `GET /api/gallery/uploads` | Список uploads |
| `POST /api/upload` | Загрузка файла в **существующую** беседу (не создаёт чат) |
| `POST /api/conversations` | Создать пустую беседу без картинки |
| `POST /api/conversations/{id}/turn` | Отправить сообщение агенту без WebSocket |
| `ws://host/ws/{conversation_id}` | Отправка из UI чата (`user_message`) |

---

## Чеклист для клиента

- [ ] Выбран формат: JSON (`asset_id`) или multipart (файл).
- [ ] Передан ровно один источник в `image`.
- [ ] При `AUTH_ENABLED` — cookie сессии в запросе.
- [ ] `text` содержит осмысленный prompt, если планируется сразу `turn`.
- [ ] `preset_slug` соответствует задаче (`img2img` для доработки фото).
- [ ] После 201: открыт `chat_url` **или** вызван `turn` с `attachment_ids[0]`.
- [ ] Ошибки обрабатываются по `detail`, кнопка разблокируется при failure.
