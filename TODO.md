# web-chat — архитектура, принципы и план разработки

> **Домашняя директория проекта:** `/root/web-chat`  
> **Язык:** Python 3.11+  
> **Назначение документа:** единый гайдлайн для всех, кто создаёт и сопровождает проект.  
> Читать последовательно; этапы выполнять **по порядку**, не перескакивая без завершения критериев готовности.

> **Статус реализации (2026-05-15):** этапы **1–7** выполнены; этап **8** в основном готов (UI в LAN); этапы **9–10** частично; этап **11** не начат. Автотесты: `pytest` (40 тестов). Детали — [журнал прогресса](#журнал-прогресса).

---

## Содержание

0. [Видение и цели](#0-видение-и-цели)  
1. [Архитектура (высокий уровень)](#1-архитектура-высокий-уровень)  
2. [Принципы программирования](#2-принципы-программирования)  
3. [Структура пакета и декларативность](#3-структура-пакета-и-декларативность)  
4. [Этапы разработки (1–11)](#4-этапы-разработки-111)  
5. [Маппинг кода из существующих проектов](#5-маппинг-кода-из-существующих-проектов)  
6. [Системные промпты (seed)](#6-системные-промпты-seed)  
7. [Чеклист перед production](#7-чеклист-перед-production)  
8. [Риски и митигация](#8-риски-и-митигация)  
9. [AI-агент и tool calling (детально)](#9-ai-агент-и-tool-calling-детально)  
10. [Фронтенд: структура и поведение](#10-фронтенд-структура-и-поведение)  
11. [REST API: полные контракты](#11-rest-api-полные-контракты)  
12. [Интеграция с image-gen (192.168.88.16)](#12-интеграция-с-image-gen-1921688816)  
13. [Обработка ошибок](#13-обработка-ошибок)  
14. [Тестирование](#14-тестирование)  
15. [Деплой и сеть (LAN / WireGuard)](#15-деплой-и-сеть-lan--wireguard)  
16. [Seed-данные пресетов (полные тексты)](#16-seed-данные-пресетов-полные-тексты)  
17. [Дорожная карта v2](#17-дорожная-карта-v2)  
18. [Зависимости (requirements)](#18-зависимости-requirements)  
19. [Критерий готовности MVP](#19-критерий-готовности-mvp)

---

## 0. Видение и цели

### 0.1. Что строим

**Монолитное Python-приложение** для работы в **локальной сети** (LAN, адреса вида `192.168.88.x`). Один процесс объединяет три логические роли:

> **Сеть и MVP:** на этапе MVP достаточно доступа по LAN (браузер → `http://<хост>:8090`). Настройку **WireGuard** не включаем в обязательные этапы 1–8 — только закладываем в архитектуру и документацию как целевой вариант удалённого доступа (см. [раздел 15](#15-деплой-и-сеть-lan--wireguard)).

| Компонент | Назначение | Где живёт в коде |
|-----------|------------|------------------|
| **Web-чат** | UI в браузере: беседы, вложения, стриминг, пресеты | `templates/`, `static/`, `app/api/websocket.py` |
| **AI-агент** | Оркестрация: история → LLM → tools → ответ | `app/services/agent_orchestrator.py` |
| **MCP-сервер (встроенный)** | Инструменты SD + документы; хранение и раздача PNG | `app/integrations/mcp_server.py`, `sd_tools.py` |

Внешние сервисы (уже развёрнуты, не входят в репозиторий web-chat):

| Сервис | URL | Роль |
|--------|-----|------|
| LLM (OpenAI-compatible) | `http://192.168.88.41:8989/v1/` | Чат, vision, function/tool calling |
| SD WebUI (Automatic1111, флаг `--api`) | `http://192.168.88.52:7860/` | txt2img, img2img, upscale |
| image-gen (референс) | `http://192.168.88.16:8081/mcp` | **Образец** реализации MCP+SD; в проде логика **переносится внутрь** web-chat |

#### Ключевой принцип из image-gen

> **В контекст LLM не попадает base64 изображений.**  
> MCP и агент возвращают только **текст + HTTP URL**.  
> Браузер и модель ссылаются на один и тот же `PUBLIC_BASE_URL`.

Это снижает расход контекста, ускоряет ответы и совпадает с проверенной архитектурой `/root/image-gen`.

### 0.2. Пользовательские сценарии (обязательные для MVP)

1. Пользователь в локальной сети открывает чат в браузере → создаёт беседу → пишет «нарисуй кота в космосе».
2. Агент вызывает LLM → модель запрашивает `generate_image` → MCP → SD → PNG в `data/generated/` → **ingest в SQLite** (`MediaAsset`) → URL `/media/asset/{uuid}` → события WS `image` и сетка `.message-images` под ответом. В `content_text` ассистента **нет** markdown `![...](url)` — только обычный текст.
3. Пользователь прикрепляет PDF и/или изображения:
   - **изображения** → multimodal-сообщение в LLM (vision);
   - **PDF/DOCX/TXT** → извлечение текста **до** или **через** tool `extract_text`.
4. Несколько вложений и несколько сгенерированных изображений отображаются в одном ответе ассистента.
5. Пресет системного промпта выбирается при создании беседы; для новых бесед по умолчанию — пресет с `is_default=true` в БД.

### 0.3. Не-цели версии 1 (v1)

- Публичный доступ в интернет (внешний firewall / проброс портов на роутере).
- Полноценная настройка WireGuard в рамках MVP (достаточно LAN; WG — см. раздел 15).
- n8n и внешние оркестраторы (опционально позже).
- Отдельный SPA на React/Vue (достаточно Jinja2 + vanilla JS).
- Микросервисное разбиение на несколько репозиториев.
- RAG / embeddings по документам (отдельный проект v2).
- Обязательная замена image-gen на .16 до стабилизации web-chat (можно держать параллельно).

### 0.4. Референсные проекты

| Путь | Что берём |
|------|-----------|
| `/root/image-gen` | MCP+SD, сохранение файлов, `safe_filename`, streamable HTTP, галерея |
| `/root/prompt-extension` | UX чата: стриминг, markdown, error banner, reasoning, темы, пресеты в UI |

**Не копировать слепо:** prompt-extension завязан на Chrome Extension API (`chrome.runtime`, content script). В web-chat — только серверный API и WebSocket.

---

## 1. Архитектура (высокий уровень)

### 1.1. Схема системы

```
┌─────────────────────────────────────────────────────────────────┐
│  Браузер пользователя (LAN; позже — через WireGuard)             │
│  HTML / CSS / JS — UI на базе prompt-extension                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP(S): REST + статика
                             │ WebSocket: /ws/{conversation_id}
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  web-chat — один процесс Uvicorn                                 │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ Web UI      │  │ REST API     │  │ WebSocket               │ │
│  │ GET /       │  │ /api/...     │  │ /ws/{id}                │ │
│  │ /static     │  │ /api/upload  │  │ стриминг ответа агента  │ │
│  │ /media/...  │  │ /health      │  └───────────┬─────────────┘ │
│  └─────────────┘  └──────────────┘              │               │
│                                                  ▼               │
│                    ┌─────────────────────────────────────────┐   │
│                    │ AgentOrchestrator                        │   │
│                    │  • сбор messages + tools                 │   │
│                    │  • commit user-msg до долгих tools/SD    │   │
│                    │  • цикл tool_calls (до MAX_TOOL_ROUNDS)  │   │
│                    │  • события в WebSocket                   │   │
│                    └───────┬─────────────────┬─────────────────┘   │
│                            │                 │                     │
│              ┌─────────────▼─────┐   ┌───────▼──────────────┐     │
│              │ LLMClient         │   │ ToolExecutor         │     │
│              │ OpenAI async SDK  │   │ in-process вызов     │     │
│              │ → 192.168.88.41   │   │ MCP-функций / extract  │     │
│              └───────────────────┘   └───────┬──────────────┘     │
│                                              │                     │
│                    ┌─────────────────────────▼──────────────┐     │
│                    │ FastMCP (streamable-http)  :8091/mcp      │     │
│                    │  generate_image, extract_text, …        │     │
│                    │  → POST SD WebUI 192.168.88.52          │     │
│                    │  → data/generated/ → ingest MediaAsset  │     │
│                    └─────────────────────────────────────────┘     │
│                                                                  │
│  ┌──────────────┐  ┌──────────────────────────────────────────┐  │
│  │ SQLite       │  │ Файлы + MediaAsset (BLOB в SQLite)        │  │
│  │ SQLAlchemy   │  │ uploads/ — документы; generated/ — SD     │  │
│  │ async + WAL  │  │ GET /media/asset/{id} — раздача из БД     │  │
│  └──────────────┘  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
  http://192.168.88.41:8989/v1/      http://192.168.88.52:7860/
```

### 1.2. Почему монолит, а не «чат + внешний MCP на .16»

| Аргумент | Пояснение |
|----------|-----------|
| Единый `PUBLIC_BASE_URL` | LLM, MCP и браузер отдают/запрашивают одни и те же URL картинок |
| Меньше сетевых сбоев | Нет лишнего hop «чат → 192.168.88.16 → SD» |
| Проверенный код | Портирование `image-gen` блоками, а не переписывание |
| Один systemd-unit | Проще деплой и логи на хосте в LAN |
| Endpoint `/mcp` | Остаётся для отладки (MCP Inspector) и внешних клиентов на переходный период |

**Исключение:** на этапе миграции image-gen на .16 может работать параллельно; история чата со старыми URL с .16 после миграции не будет открывать картинки — это ожидаемо.

### 1.3. Поток одного сообщения пользователя

```
1. Client: POST /api/upload (опционально) → attachment_ids[]
2. Client WS: { type: "user_message", text, attachment_ids }
3. Server:
   a. Валидация Pydantic
   b. Сохранить Message(role=user) в БД и **commit** (до долгих LLM/SD/tools — иначе SQLite lock)
   c. AttachmentService: подготовить vision URL / extracted_text
   d. AgentOrchestrator.run_turn(...)
4. Цикл агента:
   a. LLM.chat.completions(stream=True, messages, tools)
   b. Если tool_calls → ToolExecutor → результаты → messages += tool
   c. Повтор до финального текста или MAX_TOOL_ROUNDS
5. Server WS: text_delta | tool_* | image | done
6. Сохранить Message(role=assistant): `content_text` без markdown-картинок; `content_json` — `images`, `image_asset_ids`, tool_calls
```

### 1.4. Клиент–сервер: разделение ответственности

| Слой | Ответственность |
|------|-----------------|
| **Браузер** | Отображение, локальное состояние UI, WebSocket, markdown, превью файлов |
| **REST** | CRUD бесед, загрузка файлов, история при открытии вкладки, health |
| **WebSocket** | Только интерактивный turn (отправка сообщения + стриминг ответа) |
| **Agent** | Бизнес-логика LLM+tools, без знания о HTML |
| **MCP/SD** | Генерация файлов и текстовые отчёты с URL |

**Принцип:** после перезагрузки страницы история **всегда** восстанавливается через REST; WebSocket не хранит состояние между сессиями.

### 1.5. Протокол WebSocket

**Подключение:** `GET /ws/{conversation_id}`  
При подключении сервер может отправить `{ "type": "connected", "conversation_id": "..." }`.

#### Клиент → сервер

| type | Поля | Описание |
|------|------|----------|
| `user_message` | `text`, `attachment_ids[]` | Новый запрос пользователя |
| `cancel` | — | Отмена текущей генерации (LLM stream) |
| `regenerate` | `message_id` | Перегенерация ответа ассистента (удаление хвоста истории после user-msg) |
| `ping` | — | Keepalive; сервер отвечает `pong` |

#### Сервер → клиент

| type | Поля | Описание |
|------|------|----------|
| `connected` | `conversation_id` | Подтверждение сессии WS |
| `ack` | `user_message_id` | Сообщение пользователя сохранено |
| `text_delta` | `content` | Часть текста ассистента |
| `reasoning_delta` | `content` | Опционально: «размышления» модели |
| `tool_start` | `name`, `arguments` | Начало вызова инструмента |
| `tool_done` | `name`, `summary` | Краткий итог (без base64) |
| `image` | `urls[]`, `thumbs[]?` | Новые картинки для вставки в сообщение |
| `error` | `message`, `code` | Ошибка (см. раздел 13) |
| `done` | `assistant_message_id` | Turn завершён |
| `pong` | — | Ответ на ping |

**Нюанс:** событие `image` может прийти **до** финального `text_delta`, пока модель ещё генерирует текст. UI добавляет превью в сетку `.message-images` текущего пузыря ассистента, не дожидаясь `done`. Статус («Генерация…») — в `.message-status` внутри пузыря, не в отдельном footer.

### 1.6. Модель данных (SQLAlchemy 2.0, async)

```text
Preset
  id              UUID PK
  name            str          # «По умолчанию», «Генерация изображений»
  slug            str unique   # default, image_gen, document_analysis
  system_prompt   text         # полный системный промпт
  is_default      bool         # ровно один True в БД
  sort_order      int
  created_at      datetime

Conversation
  id              UUID PK
  title           str
  preset_id       FK → Preset
  created_at      datetime
  updated_at      datetime   # обновлять при новом сообщении

Message
  id              UUID PK
  conversation_id FK
  role            enum: user | assistant | system | tool
  content_text    text nullable    # плоский текст для поиска/отображения
  content_json    JSON nullable    # parts, tool_calls, image_urls, reasoning
  created_at      datetime

Attachment
  id              UUID PK
  conversation_id FK nullable    # привязка до отправки
  message_id      FK nullable    # после отправки
  original_name   str
  mime_type       str
  size_bytes      int
  storage_path    str            # документы: data/uploads/; image/* может быть в MediaAsset
  extracted_text  text nullable  # кэш после extract
  created_at      datetime

MediaAsset
  id              UUID PK
  conversation_id FK nullable
  mime_type       str
  data            bytes          # PNG/WebP в SQLite
  thumb_data      bytes nullable
  original_name   str nullable
  created_at      datetime
```

**Принцип нормализации:** `content_text` дублирует основной текст для простых запросов; полная структура — в `content_json`, чтобы не ломать multimodal при повторной загрузке истории.

### 1.7. Файловая система проекта

```text
/root/web-chat/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI factory, lifespan
│   ├── config.py               # pydantic-settings (декларативный конфиг)
│   ├── db/
│   │   ├── session.py, sqlite.py, migrate.py
│   │   ├── models.py, repositories.py, seed.py
│   ├── api/
│   │   ├── router.py, conversations.py, presets.py
│   │   ├── upload.py, messages.py, media.py
│   │   ├── health.py, logs_api.py, websocket.py
│   ├── services/
│   │   ├── attachment_service.py, media_service.py
│   │   ├── message_builder.py, agent_orchestrator.py
│   │   └── ws_manager.py
│   └── integrations/
│       ├── llm_client.py, tool_executor.py, tool_definitions.py
│       ├── mcp_server.py, sd_tools.py, document_tools.py
│       ├── media_utils.py, document_extractor.py
│       └── ...
├── data/
│   ├── db/web_chat.sqlite
│   ├── uploads/{attachment_id}/
│   └── generated/
│       └── thumbs/
├── static/
│   ├── css/chat.css
│   └── js/
│       ├── markdown.js
│       └── chat.js
├── templates/
│   └── chat.html
├── tests/
├── deploy/
│   └── web-chat.service
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
└── README.md
```

Каталог `data/` в `.gitignore`; в репозитории только `.gitkeep` при необходимости.

### 1.8. Конфигурация (.env)

Все настройки — через **переменные окружения** и класс `Settings` (pydantic-settings). Никаких «магических» констант в середине модулей.

```env
# --- Сервер web-chat ---
WEB_HOST=0.0.0.0
WEB_PORT=8090
# URL, который видит БРАУЗЕР пользователя (критично для картинок!)
PUBLIC_BASE_URL=http://192.168.88.100:8090

# --- LLM ---
LLM_BASE_URL=http://192.168.88.41:8989/v1
LLM_API_KEY=
LLM_MODEL=                    # пусто = авто через GET /v1/models
LLM_TIMEOUT_SEC=300

# --- Stable Diffusion WebUI ---
SD_WEBUI_URL=http://192.168.88.52:7860
SD_AUTH_USER=
SD_AUTH_PASS=
REQUEST_TIMEOUT=600             # секунды, запрос к SD
MCP_TIMEOUT=900                 # должно быть > REQUEST_TIMEOUT

# --- БД и лимиты ---
DATABASE_URL=sqlite+aiosqlite:///./data/db/web_chat.sqlite
MAX_UPLOAD_MB=25
MAX_FILES_PER_MESSAGE=10
MAX_TOOL_ROUNDS=10
MAX_HISTORY_MESSAGES=60         # пар user/assistant в контекст LLM

# --- Хранение ---
UPLOAD_RETENTION_DAYS=7
GENERATED_RETENTION_DAYS=30
```

**Валидация при старте:** если `MCP_TIMEOUT <= REQUEST_TIMEOUT` — warning в лог (как в image-gen `validate_settings()`).

### 1.9. Интеграция LLM

- Клиент: `openai.AsyncOpenAI(base_url=..., api_key=...)`.
- Стриминг: `chat.completions.create(..., stream=True)`.
- Tools: JSON Schema в формате OpenAI; имена **совпадают** с MCP tools.
- Vision: предпочтительно `image_url` с `PUBLIC_BASE_URL/media/uploads/...`, не base64 (настраиваемый fallback `USE_BASE64_IMAGES=false`).

### 1.10. MCP и SD

- Библиотека: **FastMCP** (как image-gen).
- Transport: `streamable-http`, путь `/mcp`.
- Запуск: фоновый `threading.Thread` (daemon), основной поток — Uvicorn (паттерн из `image-gen/code/app/server.py`).
- Инструменты v1: `generate_image`, `extract_text`.
- Инструменты v2 (этап 11): `img2img`, `upscale_images`, `get_gallery`.

#### Пайплайн сгенерированных изображений (текущая реализация)

```text
generate_image (count 1–10, n_iter=count, batch_size=1)
  → SD WebUI → base64 → data/generated/{file}.png + thumbs/
  → ingest_sd_output_files → MediaAsset в SQLite
  → публичный URL: {PUBLIC_BASE_URL}/media/asset/{uuid}
  → content_json.images + image_asset_ids; WS type=image
  → UI: .message-images (не markdown в content_text)
```

Legacy `/media/generated/{file}` остаётся для отладки и импорта старых URL; в новых ответах ассистента предпочтителен `/media/asset/`.

### 1.11. Обработка вложений

| MIME / тип | Действие до LLM | Tool |
|------------|-----------------|------|
| `image/*` | URL в multimodal `content` | — |
| `application/pdf` | PyMuPDF → текст в `content` | `extract_text` при необходимости |
| DOCX | `python-docx` | то же |
| `text/*`, csv | чтение файла | то же |
| прочее | отклонить на upload с 415 | — |

**Два пути для документов (осознанно):**

1. **Eager (рекомендуется):** `AttachmentService` извлекает текст сразу после upload или перед `run_turn` — модель всегда видит документ.
2. **Lazy (tool):** модель сама вызывает `extract_text(attachment_id)` — для больших файлов или уточняющих вопросов.

### 1.12. Безопасность

**MVP (LAN):**

- Сервис слушает `0.0.0.0` или IP хоста в локальной сети; не пробрасывать порт на интернет без необходимости.
- Доверять сегменту LAN (домашняя/лабораторная сеть с LLM и SD).

**Позже (WireGuard):**

- Вынести UI за VPN; снаружи — только WG, без публичного HTTP.
- Те же правила `PUBLIC_BASE_URL`: URL в ответах должны быть достижимы из браузера пользователя (уже через туннель).

**Всегда:**
- `safe_filename()` + `Path.resolve().is_relative_to()` для всех путей (порт из image-gen).
- MCP: запрет внешних URL в `img2img`/upscale — только `PUBLIC_BASE_URL` и локальные имена файлов.
- Санитизация HTML на клиенте (`sanitizeHtml` из prompt-extension).
- Секреты только в `.env`, файл в `.gitignore`.
- Rate limit (in-memory): uploads и `generate_image` на IP/сессию — простая защита от злоупотребления.

---

## 2. Принципы программирования

### 2.1. PEP 8 и стиль Python

- Отступы 4 пробела; длина строки до 100–120 символов (зафиксировать в `pyproject.toml` / Ruff).
- Имена: `snake_case` для функций и переменных, `PascalCase` для классов, `UPPER_SNAKE` для констант модуля.
- Импорты: stdlib → third-party → local, разделены пустой строкой.
- Type hints на **всех** публичных функциях и методах.
- `from __future__ import annotations` в новых модулях для отложенных аннотаций.

### 2.2. Документация на русском языке

**Обязательно на русском:**

- Модульные docstring в начале каждого файла (кратко: назначение модуля).
- Docstring публичных классов и функций (Google style).
- Комментарии к нетривиальной логике (почему, а не что).

**Пример модуля:**

```python
"""
Оркестратор диалога с LLM и инструментами.

Отвечает за цикл: запрос к LLM → tool_calls → выполнение → повтор.
Не знает о WebSocket напрямую: получает callback для отправки событий.
"""
```

**Пример функции:**

```python
async def run_turn(
    self,
    conversation_id: uuid.UUID,
    user_text: str,
    attachment_ids: list[uuid.UUID],
    emit: EventEmitter,
) -> Message:
    """
    Выполнить один ход диалога (сообщение пользователя → ответ ассистента).

    Args:
        conversation_id: Идентификатор беседы.
        user_text: Текст сообщения пользователя.
        attachment_ids: Список UUID вложений, уже сохранённых через upload.
        emit: Async-функция для отправки событий в WebSocket (text_delta, image, …).

    Returns:
        Сохранённое сообщение ассистента с заполненным content_json.

    Raises:
        ToolLoopExceeded: Превышен лимит MAX_TOOL_ROUNDS.
        LLMError: Ошибка или таймаут LLM.
    """
```

**На английском допустимо:** имена переменных, поля JSON API, названия MCP tools (совместимость с SD/OpenAI).

### 2.3. Декларативный подход

| Область | Декларативно (что) | Императивно (как) — изолировать |
|---------|-------------------|----------------------------------|
| Конфиг | `Settings` в `config.py` | — |
| Схемы API | Pydantic models в `api/schemas.py` | — |
| ORM | SQLAlchemy `Mapped`, `mapped_column` | — |
| Tools для LLM | Список `TOOL_DEFINITIONS` | Выполнение в `ToolExecutor` |
| Маршруты | `APIRouter` декларации | Тонкие handlers → сервисы |
| MCP tools | `@mcp.tool()` декораторы | Тело вызывает SD |

**Правило:** роутер WebSocket не должен содержать цикл tool calling — только вызов `AgentOrchestrator.run_turn()`.

### 2.4. Слои и зависимости

```text
api/  →  services/  →  integrations/  →  db/
         ↓
      repositories (db)
```

- **api/** — HTTP/WS, валидация входа, коды ответов.
- **services/** — бизнес-логика, транзакции, оркестрация.
- **integrations/** — внешние системы (LLM, SD, файлы).
- **db/** — модели и запросы к БД.

**Запрещено:** импорт `api` из `integrations`; прямой SQL в роутерах.

### 2.5. Асинхронность

- FastAPI handlers и WS — `async def`.
- SQLAlchemy 2.0 — `AsyncSession`.
- HTTP к SD — `httpx.AsyncClient` (или `requests` в thread pool для портированного кода image-gen на первом этапе; затем мигрировать на httpx).
- Блокирующие вызовы (PIL, тяжёлый PDF) — `asyncio.to_thread()` чтобы не блокировать event loop.

### 2.6. Логирование

```python
logger = logging.getLogger(__name__)

logger.info(
    "Вызов инструмента %s, беседа=%s",
    tool_name,
    conversation_id,
    extra={"tool": tool_name, "conversation_id": str(conversation_id)},
)
```

Уровни: INFO — шаги пользователя; DEBUG — payload LLM (без секретов); WARNING — таймауты; ERROR — исключения с `exc_info=True`.

### 2.7. Обработка ошибок

- Пользователю — понятное сообщение на русском в WS `error` или HTTP JSON.
- Внутри — цепочка исключений (`LLMError`, `SDError`, `ValidationError`).
- Не глотать исключения без лога; не возвращать сырой traceback клиенту.

### 2.8. Тестируемость

- Сервисы принимают зависимости через конструктор (DI): `LLMClient`, `ToolExecutor`, `Session`.
- Для тестов — mock/fake LLM, фикстуры SQLite in-memory.

---

## 3. Структура пакета и декларативность

### 3.1. Точка входа `app/main.py`

```python
"""
Точка входа FastAPI-приложения web-chat.

Создаёт приложение, подключает роутеры, монтирует статику,
в lifespan — инициализация БД и запуск MCP в фоновом потоке.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.websocket import ws_router
from app.config import settings
from app.db.session import init_db
from app.integrations.mcp_server import start_mcp_background


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Инициализация при старте и остановка при выключении."""
  await init_db()
  mcp_thread = start_mcp_background()
  yield
  # MCP daemon thread завершится вместе с процессом


def create_app() -> FastAPI:
  """Фабрика приложения (удобно для тестов)."""
  app = FastAPI(title="web-chat", lifespan=lifespan)
  app.include_router(api_router, prefix="/api")
  app.include_router(ws_router)
  app.mount("/static", StaticFiles(directory="static"), name="static")
  # /media — отдельный router с проверкой safe_filename
  return app


app = create_app()
```

### 3.2. Декларативный конфиг `app/config.py`

```python
"""
Настройки приложения из переменных окружения.

Все значения по умолчанию заданы здесь; переопределение — через .env.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
  """Центральный конфиг web-chat."""

  model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
  )

  web_host: str = "0.0.0.0"
  web_port: int = 8090
  public_base_url: str = "http://localhost:8090"

  llm_base_url: str = "http://192.168.88.41:8989/v1"
  llm_api_key: str = ""
  llm_model: str = ""
  llm_timeout_sec: int = 300

  sd_webui_url: str = "http://192.168.88.52:7860"
  request_timeout: int = 600
  mcp_timeout: int = 900

  database_url: str = "sqlite+aiosqlite:///./data/db/web_chat.sqlite"
  max_upload_mb: int = 25
  max_files_per_message: int = 10
  max_tool_rounds: int = 10
  max_history_messages: int = 60


settings = Settings()
```

### 3.3. Pydantic-схемы API (фрагмент)

```python
"""Схемы запросов и ответов REST API."""

from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class ConversationCreate(BaseModel):
  """Тело запроса на создание беседы."""

  title: str | None = Field(None, max_length=200)
  preset_id: UUID | None = Field(
    None,
    description="Если не указан — используется пресет с is_default=true",
  )


class ConversationOut(BaseModel):
  """Беседа в ответе API."""

  id: UUID
  title: str
  preset_id: UUID
  created_at: datetime
  updated_at: datetime

  model_config = {"from_attributes": True}
```

---

## 4. Этапы разработки (1–11)

Каждый этап завершается только когда выполнены **все** пункты «Проверка».  
Отмечать прогресс: `[ ]` → `[x]`.

---

### Этап 1. Каркас проекта и конфигурация ✅

**Цель:** запускаемый FastAPI с health, настройками и структурой каталогов.

**Задачи:**

- [x] Создать дерево каталогов (раздел 1.7).
- [x] `pyproject.toml` — Ruff/black, pytest, Python >=3.11.
- [x] `requirements.txt` (раздел 18).
- [x] `app/config.py` — `Settings`, валидация `mcp_timeout > request_timeout`.
- [x] `app/main.py` — `create_app()`, `GET /health`.
- [x] `.env.example`, `.gitignore` (`data/`, `.env`, `__pycache__`, `.venv`).
- [x] `README.md` — как запустить, ссылки на LLM/SD URL.
- [x] `deploy/web-chat.service` — шаблон systemd.

**Реализовано сверх этапа 1:** `/health` проверяет LLM и SD (`status: ok | degraded`), не только живость процесса.

**Пример health (минимум этапа 1; в коде — расширенный):**

```python
@router.get("/health")
async def health() -> dict[str, str]:
  """Проверка живости процесса (без внешних зависимостей)."""
  return {"status": "ok"}
```

**Проверка:**

```bash
cd /root/web-chat && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8090
curl -s http://localhost:8090/health
# → {"status":"ok"}
```

---

### Этап 2. База данных и REST для бесед ✅

**Цель:** CRUD бесед и пресетов без чата и WS.

**Задачи:**

- [x] `app/db/models.py` — Preset, Conversation, Message (полная модель).
- [x] `app/db/session.py` — `async_sessionmaker`, `init_db()` → `create_all`.
- [x] `app/db/repositories.py` — `ConversationRepository`, `PresetRepository`.
- [x] Seed при первом старте: 3 пресета (раздел 16), один `is_default=True`.
- [x] `GET/POST /api/conversations`, `GET/PATCH/DELETE /api/conversations/{id}`.
- [x] `GET /api/presets`.
- [x] `POST /api/presets/{id}/set-default` — переключить default для новых бесед.

**Дополнительно:** `migrate.py` (обновление пресетов в существующей БД), `sqlite.py` (WAL, retry записи).

**Нюанс:** при `POST /api/conversations` без `preset_id` — SQL:

```python
preset = await preset_repo.get_default()
if preset is None:
  raise HTTPException(500, "Не настроен пресет по умолчанию")
```

**Проверка:**

```bash
curl -X POST http://localhost:8090/api/conversations -H "Content-Type: application/json" -d '{}'
curl http://localhost:8090/api/conversations
curl http://localhost:8090/api/presets
```

---

### Этап 3. Загрузка файлов ✅

**Цель:** multipart upload, метаданные в БД, безопасная раздача.

**Задачи:**

- [x] Модель `Attachment`.
- [x] `POST /api/upload` — поле `files[]`, несколько файлов.
- [x] Валидация: размер, MIME whitelist, `max_files_per_message`.
- [x] Сохранение: `data/uploads/{attachment_id}/{safe_name}` (документы); изображения — также `MediaAsset`.
- [x] `GET /media/uploads/{attachment_id}/{filename}` — `FileResponse` после `safe_filename`.
- [x] `AttachmentService.register_upload()` — запись в БД.

**Пример проверки MIME:**

```python
ALLOWED_MIMES = frozenset({
  "image/jpeg", "image/png", "image/webp", "image/gif",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/plain", "text/csv",
})
```

**Проверка:** загрузить PNG + PDF → получить два `id` → открыть preview URL для PNG в браузере.

---

### Этап 4. Встроенный MCP + SD (порт image-gen) ✅

**Цель:** генерация изображений из процесса web-chat.

**Задачи:**

- [x] Скопировать и адаптировать `media_utils.py` из `image-gen` (`safe_filename`, `save_image_from_base64`, `make_thumbnail`).
- [x] `sd_tools.py` — `register_sd_tools(mcp)` с `generate_image`.
- [x] `mcp_server.py` — FastMCP, `start_mcp_background()` на порту `WEB_PORT+1` (8091).
- [x] `data/generated/`, `data/generated/thumbs/`.
- [x] `GET /media/generated/{filename}`, `/media/generated/thumbs/{filename}`.
- [x] Расширить `/health` — запрос к SD.

**Отличие от image-gen:** параметр `count` (1–10) → `n_iter=count`, `batch_size=1` за один вызов tool (в image-gen — только `n_iter: 1`, несколько картинок = несколько вызовов).

**URL после ingest (основной путь в чате):**

```python
# media_service / media_utils
f"{settings.public_base_url.rstrip('/')}/media/asset/{asset_id}"
```

Отчёт MCP/tool по-прежнему может содержать `URL: .../media/generated/...` до ingest; оркестратор нормализует в `/media/asset/`.

**Проверка:** MCP Inspector или `test_agent` → файлы в `data/generated/`, в чате — `/media/asset/{uuid}`.

---

### Этап 5. LLM-клиент и ToolExecutor ✅

**Цель:** цикл tool calling без UI.

**Задачи:**

- [x] `llm_client.py` — `complete()`, `stream()` через AsyncOpenAI.
- [x] `TOOL_DEFINITIONS` — декларативный список (раздел 9.1).
- [x] `tool_executor.py` — маршрутизация по имени; **in-process** вызов функций из `sd_tools`.
- [x] `agent_orchestrator.py` — цикл до `max_tool_rounds`.
- [x] `scripts/test_agent.py` — CLI для ручной проверки.

**Дополнительно:** `await session.commit()` после сохранения user-сообщения до долгих SD/tools (избежание `database is locked`).

**Нюанс in-process vs MCP HTTP:**

| Подход | Плюсы | Минусы |
|--------|-------|--------|
| In-process | Скорость, проще отладка | Дублирование регистрации tool |
| HTTP localhost `/mcp` | Один путь выполнения | Лишняя сеть |

**Рекомендация:** ToolExecutor вызывает Python-функции напрямую; MCP endpoint — для внешних клиентов и тестов.

**Проверка:**

```bash
python -m app.scripts.test_agent "Нарисуй закат над морем"
# В stdout — URL (generated и/или /media/asset/ после ingest)
```

---

### Этап 6. Document extractor ✅

**Цель:** текст из документов для LLM.

**Задачи:**

- [x] `document_extractor.py`:
  - PDF — `fitz` (PyMuPDF);
  - DOCX — `python-docx`;
  - TXT/CSV — utf-8 с fallback;
  - изображения — опционально `pytesseract` (если установлен tesseract).
- [x] MCP tool `extract_text(attachment_id, max_chars)` + in-process в ToolExecutor.
- [x] `AttachmentService.prepare_for_llm()` — eager extract при отправке сообщения.
- [x] Обрезка текста + суффикс «… (обрезано, всего N символов)».

**Проверка:** upload PDF → test extract → непустой текст, длина <= max_chars.

---

### Этап 7. WebSocket и сохранение истории ✅

**Цель:** полный серверный цикл чата.

**Задачи:**

- [x] `ConnectionManager` — словарь `conversation_id → set[WebSocket]` (несколько вкладок).
- [x] Обработка `user_message`, `cancel`, `ping`, **`regenerate`**.
- [x] `GET /api/conversations/{id}/messages` — пагинация `limit`, `before`; enrich URL при отдаче.
- [x] Сбор `messages` для LLM (`message_builder`, раздел 9.3).
- [x] Стриминг всех типов событий WS.
- [x] Сохранение `Message` user + assistant с `content_json`.

**Пример content_json ассистента (текущий формат):**

```json
{
  "images": ["/media/asset/550e8400-e29b-41d4-a716-446655440000"],
  "image_asset_ids": ["550e8400-e29b-41d4-a716-446655440000"],
  "tool_calls": [{"name": "generate_image", "id": "..."}],
  "reasoning": null
}
```

`content_text` — только проза; markdown `![...](url)` удаляется (`finalize_assistant_text` / `strip_markdown_images`).

**Сверх этапа 7:** `PATCH`/`DELETE` сообщений, `run_regenerate_turn`, API логов (`GET/DELETE /api/logs`).

**Нюанс отмены:** `cancel` устанавливает `asyncio.Event`; stream LLM прерывается; запрос SD может завершиться в фоне — сообщить пользователю честно.

**Проверка:** websocat/wscat — текстовый ответ; запрос картинки — события `tool_start`, `image`, `done`.

---

### Этап 8. UI чата (порт prompt-extension) — в основном ✅

**Цель:** рабочий браузерный интерфейс.

**Задачи:**

- [x] `templates/chat.html` — layout: sidebar бесед, chat, input (раздел 10.1).
- [x] `static/css/chat.css` — порт CSS variables и компонентов из `prompt-extension/sidebar.css`.
- [x] `static/js/markdown.js` — `formatMarkdown`, `sanitizeHtml`, `parseTables` (порядок: image до link).
- [x] `static/js/chat.js` — REST + WS (раздел 10.2).
- [x] Список бесед, создание, переключение.
- [x] Пресет: dropdown / sidebar-settings + отображение текущего.
- [x] Превью вложений, multi-file, drag-drop.
- [x] Рендер `image` events — grid `.message-images` + lightbox (prev/next, swipe, клавиатура).

**Дополнительно:** редактирование/удаление сообщений, перегенерация, модалка логов, sticky auto-scroll, статус в `.message-status` внутри пузыря.

**Проверка:** в браузере по LAN (`http://<хост>:8090`) — полный сценарий: текст + генерация + PDF. После смены static — **Ctrl+F5**.

---

### Этап 9. Пресеты, настройки, полировка UX — частично

**Задачи:**

- [x] Default preset для новых бесед из API.
- [x] Панель настроек (`sidebar-settings`): пресет, connection pill, кнопки; тема light/dark (`localStorage`).
- [ ] Модель в UI (readonly из server / override) — не реализовано.
- [ ] Размер шрифта в настройках — не реализовано.
- [x] Progress при `tool_start` + `generate_image` — в `.message-status` внутри пузыря ассистента.
- [x] Error banner (порт из prompt-extension).
- [x] Прокрутка вниз (sticky zone ~100px), thinking до первого `text_delta`.

**Проверка:** смена пресета на новой беседе меняет поведение (image_gen чаще вызывает tool).

---

### Этап 10. Надёжность, логи, деплой — частично

**Задачи:**

- [x] Расширенный `/health` — llm, sd (`degraded` при сбое).
- [x] Таймауты и коды `error.code` (раздел 13).
- [ ] Cleanup по `UPLOAD_RETENTION_DAYS` / `GENERATED_RETENTION_DAYS` — только в конфиге, cron/timer не подключён.
- [x] pytest: unit + integration (40 тестов, раздел 14).
- [x] systemd, README deploy (LAN); WireGuard — в разделе 15.
- [x] SQLite WAL + retry записи (`app/db/sqlite.py`, `run_write`).

**Проверка:** остановить SD → в UI понятная ошибка; после запуска SD — снова работает.

---

### Этап 11 (опционально). img2img, upscale, галерея

**Задачи:**

- [ ] Порт `img2img`, `upscale_images`, `get_gallery` из image-gen.
- [ ] Инструкции для LLM по `denoising_strength` (см. image-gen TODO).
- [ ] Страница `/gallery` — упрощённый порт `web_server.py`.

---

## 5. Маппинг кода из существующих проектов

| Источник | Назначение в web-chat | Действие |
|----------|----------------------|----------|
| `image-gen/code/app/tools.py` | `app/integrations/sd_tools.py` | Порт `generate_image`, валидация, payload |
| `image-gen/code/app/utils.py` | `app/integrations/media_utils.py` | Порт утилит файлов |
| `image-gen/code/app/settings.py` | `app/config.py` | Перенести идеи, не дублировать весь файл |
| `image-gen/code/app/server.py` | `app/integrations/mcp_server.py` | Паттерн MCP thread + middleware |
| `image-gen/code/deploy/*` | `deploy/` | timer cleanup, service |
| `prompt-extension/sidebar.css` | `static/css/chat.css` | Адаптация селекторов |
| `prompt-extension/sidebar.js` | `static/js/markdown.js`, `chat.js` | Убрать chrome.* API |
| `prompt-extension/sidebar.html` | `templates/chat.html` | Layout + sidebar бесед |

**Принцип минимального diff:** копировать блоками, коммитить по этапам; не смешивать порт SD и UI в одном коммите.

---

## 6. Системные промпты (seed)

Краткие версии; полные тексты — в [разделе 16](#16-seed-данные-пресетов-полные-тексты).

| slug | name | is_default |
|------|------|------------|
| `default` | По умолчанию | true |
| `image_gen` | Генерация изображений | false |
| `document_analysis` | Анализ документов | false |

Seed выполнять в `init_db()` только если таблица `presets` пуста.

---

## 7. Чеклист перед production

- [ ] С хоста web-chat пингуются LLM (.41), SD (.52).
- [ ] `PUBLIC_BASE_URL` совпадает с URL в браузере пользователя.
- [ ] `MCP_TIMEOUT > REQUEST_TIMEOUT`.
- [ ] SD запущен с `--api`.
- [ ] (Опционально) WireGuard: туннель для удалённого доступа, не обязателен для первого релиза в LAN.
- [ ] `.env` не в git; права на `data/` ограничены.
- [ ] Резервное копирование `data/db/` (и при необходимости `data/generated/`).
- [ ] systemd `Restart=on-failure` включён.
- [ ] Логи ротируются (journald или logrotate).

---

## 8. Риски и митигация

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| LLM не поддерживает tools | Средняя | Проверить модель на .41; документировать совместимые |
| Долгая генерация SD | Высокая | WS progress, большие таймауты, не блокировать UI |
| Огромный PDF | Средняя | max_chars, предупреждение в чате |
| Неверный PUBLIC_BASE_URL | Высокая | Проверка в `/health` + документация |
| Дублирование MCP .16 и web-chat | Низкая | Фаза миграции (раздел 12) |
| Утечка путей через upload | Низкая | safe_filename, resolve, is_relative_to |
| Блокировка event loop | Средняя | to_thread для PIL/PDF |

---

## 9. AI-агент и tool calling (детально)

### 9.1. Декларативные определения tools для LLM

Хранить в `app/integrations/tool_definitions.py`:

```python
"""
JSON-схемы инструментов для OpenAI-compatible API.

Имена функций должны совпадать с MCP tools и обработчиками ToolExecutor.
"""

TOOL_DEFINITIONS: list[dict] = [
  {
    "type": "function",
    "function": {
      "name": "generate_image",
      "description": (
        "Сгенерировать изображение по текстовому описанию через Stable Diffusion. "
        "Возвращает текст с HTTP URL готовых PNG. "
        "Вызывай, когда пользователь просит нарисовать, сгенерировать, создать картинку."
      ),
      "parameters": {
        "type": "object",
        "properties": {
          "prompt": {
            "type": "string",
            "description": "Детальное описание изображения",
          },
          "negative_prompt": {"type": "string", "default": ""},
          "width": {"type": "integer", "default": 1024},
          "height": {"type": "integer", "default": 1024},
          "steps": {"type": "integer", "default": 22},
          "cfg_scale": {"type": "number", "default": 5.0},
          "sampler_name": {"type": "string", "default": "Euler a"},
          "seed": {"type": "integer", "default": -1},
          "count": {
            "type": "integer",
            "default": 1,
            "description": "Число вариантов (1–10), n_iter в SD",
          },
        },
        "required": ["prompt"],
      },
    },
  },
  {
    "type": "function",
    "function": {
      "name": "extract_text",
      "description": (
        "Извлечь текст из файла, загруженного пользователем "
        "(PDF, DOCX, TXT, изображение с OCR)."
      ),
      "parameters": {
        "type": "object",
        "properties": {
          "attachment_id": {"type": "string", "description": "UUID вложения"},
          "max_chars": {"type": "integer", "default": 50000},
        },
        "required": ["attachment_id"],
      },
    },
  },
]
```

### 9.2. ToolExecutor

```python
"""
Выполнение инструментов по запросу LLM.

Возвращает текстовый result для role=tool и список URL изображений для UI.
"""

import re
from dataclasses import dataclass

IMAGE_URL_RE = re.compile(
  r"URL:\s*(\S+)|(/media/(?:generated|asset)/[^\s\)]+\.(?:png|jpg|jpeg|webp)?)",
  re.IGNORECASE,
)
# Для asset без расширения в path: отдельно парсятся /media/asset/{uuid}


@dataclass
class ToolResult:
  """Результат вызова инструмента."""

  content: str
  image_urls: list[str]


class ToolExecutor:
  """Маршрутизатор вызовов tools."""

  async def run(self, name: str, arguments: dict) -> ToolResult:
    if name == "generate_image":
      text = await self._generate_image(**arguments)
      return ToolResult(content=text, image_urls=self._parse_urls(text))
    if name == "extract_text":
      text = await self._extract_text(**arguments)
      return ToolResult(content=text, image_urls=[])
    raise ValueError(f"Неизвестный инструмент: {name}")

  @staticmethod
  def _parse_urls(tool_output: str) -> list[str]:
    """Извлечь URL картинок из текстового отчёта MCP."""
    urls: list[str] = []
    for m in IMAGE_URL_RE.finditer(tool_output):
      urls.append(m.group(1) or m.group(2))
    return urls
```

### 9.3. Сборка messages для LLM

**Порядок:**

1. `system` — `Preset.system_prompt` беседы.
2. История — последние `MAX_HISTORY_MESSAGES` из БД (формат OpenAI).
3. `user` — текущее сообщение:

```python
def build_user_content(
  text: str,
  attachments: list[Attachment],
) -> list[dict]:
  """
  Собрать multimodal content для сообщения пользователя.

  Изображения — image_url; документы — текстовые блоки с extracted_text.
  """
  parts: list[dict] = [{"type": "text", "text": text}]
  for att in attachments:
    if att.mime_type.startswith("image/"):
      parts.append({
        "type": "image_url",
        "image_url": {"url": att.public_url},
      })
    elif att.extracted_text:
      parts.append({
        "type": "text",
        "text": f"[Документ: {att.original_name}]\n{att.extracted_text}",
      })
  return parts
```

**Нюанс tool messages:** после вызова инструмента обязательно:

```python
{
  "role": "tool",
  "tool_call_id": call_id,
  "content": result.content,
}
```

И перед этим — assistant message с `tool_calls` в том виде, как вернул LLM.

### 9.4. Post-process ответа ассистента

**Текущая политика (не добавлять markdown-картинки в текст):**

```python
def finalize_assistant_text(
  text: str,
  media_url_rewrites: dict[str, str] | None = None,
) -> str:
  """Нормализовать URL в prose и убрать ![alt](url) — UI покажет картинки из content_json."""
  ...
  return strip_markdown_images(body)
```

- Картинки попадают в `content_json.images` / `image_asset_ids` и в WS `image`.
- Пресет `image_gen` явно запрещает LLM вставлять `![...](url)` (раздел 16.2).
- Legacy-сообщения с markdown при `GET .../messages` очищаются через enrich.

---

## 10. Фронтенд: структура и поведение

### 10.1. Макет

```text
┌─────────────────────────────────────────────────────────────┐
│ [≡] Беседы          │  Заголовок беседы    [Персона ▼] [⚙]  │
├───────────────────┼─────────────────────────────────────────┤
│ + Новая беседа    │  [error-banner]                         │
│ ─────────────     │  [reasoning-container]                  │
│ • Беседа 1        │  ┌─────────────────────────────────┐   │
│ • Беседа 2  ◀     │  │ #chat-messages                  │   │
│                   │  │  .chat-message.user             │   │
│                   │  │  .chat-message.assistant        │   │
│                   │  └─────────────────────────────────┘   │
│                   │  [.message-status в пузыре ассистента]   │
│                   │  [attachment-preview-strip]            │
│                   │  [textarea #user-input] [📎] [Send]    │
└───────────────────┴─────────────────────────────────────────┘
```

Ширина sidebar ~260px; на узком экране — overlay.

### 10.2. Класс ChatSocket (скелет)

```javascript
/**
 * WebSocket-клиент чата.
 * Не хранит историю — только текущий turn; история с REST.
 */
class ChatSocket {
  constructor(baseUrl, conversationId, handlers) {
    this.url = `${baseUrl}/ws/${conversationId}`;
    this.handlers = handlers;
    this.ws = null;
  }

  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (e) => this._onMessage(JSON.parse(e.data));
    this.ws.onclose = () => this._scheduleReconnect();
  }

  sendUserMessage(text, attachmentIds) {
    this.ws.send(JSON.stringify({
      type: "user_message",
      text,
      attachment_ids: attachmentIds,
    }));
    this.handlers.onThinkingStart?.();
  }

  _onMessage(msg) {
    const map = {
      text_delta: () => this.handlers.onTextDelta(msg.content),
      image: () => this.handlers.onImages(msg.urls),
      tool_start: () => this.handlers.onToolStart(msg.name),
      done: () => this.handlers.onDone(msg.assistant_message_id),
      error: () => this.handlers.onError(msg.message, msg.code),
    };
    map[msg.type]?.();
  }
}
```

### 10.3. Загрузка файлов

```text
1. input[type=file][multiple] или drag-drop
2. FormData → POST /api/upload
3. Ответ → chips с именем; для image — <img src=preview_url>
4. Send → WS с attachment_ids
5. on done → очистить strip
```

### 10.4. CSS для нескольких изображений

```css
.message-images {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 8px;
  margin-top: 8px;
}

.message-images img {
  width: 100%;
  border-radius: 8px;
  cursor: zoom-in;
}
```

---

## 11. REST API: полные контракты

### 11.1. Conversations

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/conversations` | Список, сортировка `updated_at DESC` |
| POST | `/api/conversations` | Создать |
| GET | `/api/conversations/{id}` | Одна беседа |
| PATCH | `/api/conversations/{id}` | `{ "title"?, "preset_id"? }` |
| DELETE | `/api/conversations/{id}` | Удалить (каскад messages) |
| GET | `/api/conversations/{id}/messages` | История |

**Query messages:** `limit=50`, `before=<message_id>` для cursor pagination.

### 11.2. Presets

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/presets` | Все пресеты |
| POST | `/api/presets/{id}/set-default` | Установить default |

### 11.3. Upload

**POST `/api/upload`** — `multipart/form-data`, поле `files`.

**Response 200:**

```json
{
  "attachments": [
    {
      "id": "uuid",
      "original_name": "scan.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 102400,
      "preview_url": null
    }
  ]
}
```

**Ошибки:** `413` размер, `415` MIME, `400` слишком много файлов.

### 11.4. Config (опционально для UI)

**GET `/api/config`** — публичные лимиты без секретов:

```json
{
  "max_upload_mb": 25,
  "max_files_per_message": 10,
  "public_base_url": "http://192.168.88.100:8090"
}
```

---

## 12. Интеграция с image-gen (192.168.88.16)

### 12.1. Фазы миграции

| Фаза | Состояние |
|------|-----------|
| A | web-chat со встроенным MCP; SD на .52 |
| B | Cherry Studio / др. ещё на .16 — без изменений |
| C | Стабильный web-chat → остановка image-gen на .16 или только архив галереи |

### 12.2. Отличия

| image-gen | web-chat |
|-----------|----------|
| Галерея + MCP | + чат + SQLite + агент |
| Порты 8080/8081 | 8090 (+8091 MCP) |
| Нет истории диалогов | Полная история |
| Клиент — внешний LLM | LLM встроен в оркестратор |
| `n_iter: 1`, N картинок = N вызовов | `count` 1–10 за один `generate_image` |
| URL только `/media/generated/` | Основной путь: `/media/asset/{uuid}` в БД |
| LLM вставляет markdown в ответ | UI: сетка под текстом, без `![...](url)` в `content_text` |

### 12.3. Совместимость URL

Старые сообщения с URL `http://192.168.88.16:8080/images/...` после отключения .16 не загрузят картинки. При миграции не переносить старую историю или принять broken images.

---

## 13. Обработка ошибок

### 13.1. Коды WS `error.code`

| code | Когда | UI |
|------|-------|-----|
| `llm_unreachable` | connection error к .41 | error-banner |
| `llm_timeout` | timeout | error-banner |
| `sd_unreachable` | SD недоступен | «Сервер рисования недоступен» |
| `sd_generation_failed` | 4xx/5xx WebUI | деталь в логах, кратко в UI |
| `upload_rejected` | валидация | toast до send |
| `tool_loop_exceeded` | > MAX_TOOL_ROUNDS | сообщение ассистента |
| `cancelled` | user cancel | убрать progress |
| `internal_error` | необработанное | «Внутренняя ошибка» |

### 13.2. Отмена

Клиент: `{ "type": "cancel" }`. Сервер отменяет asyncio Task стрима LLM. SD может завершить генерацию — UI: «Запрос отменён; генерация на сервере может ещё выполняться».

### 13.3. Health (полный)

```json
{
  "status": "ok",
  "llm": {"ok": true, "latency_ms": 120, "model": "..."},
  "sd": {"ok": true},
  "database": {"ok": true},
  "disk_free_mb": 50000,
  "generated_count": 42
}
```

`status: "degraded"` если llm или sd недоступны, но процесс жив.

---

## 14. Тестирование

### 14.1. Unit

- `safe_filename` — path traversal, пустое имя.
- `parse_urls` / `IMAGE_URL_RE`.
- `document_extractor` — fixtures в `tests/fixtures/`.
- `build_user_content` — image + pdf.

### 14.2. Integration

```python
@pytest.mark.asyncio
async def test_agent_generate_image_mock_llm(client, mock_sd):
  """LLM возвращает tool_call → SD mock → URL в результате."""
  ...
```

### 14.3. Ручной QA (чеклист)

- [ ] Текст без tools
- [ ] «Нарисуй кота» → 1+ PNG
- [ ] PDF вопрос → ответ по содержимому
- [ ] Фото + «что на фото» → vision
- [ ] 2 беседы — истории не смешиваются
- [ ] Reload страницы — история из REST
- [ ] Пресет image_gen на новой беседе
- [ ] Остановка SD — понятная ошибка

---

## 15. Деплой и сеть (LAN / WireGuard)

### 15.1. Топология

**MVP — локальная сеть:**

```text
[ПК / ноутбук в LAN] ──HTTP──► [web-chat :8090]
                                    ├──► LLM .41:8989
                                    └──► SD .52:7860
```

Браузер открывает, например: `http://192.168.88.100:8090`.  
`PUBLIC_BASE_URL` в `.env` должен совпадать с этим адресом (см. 15.3).

**Целевая схема (после MVP) — WireGuard:**

```text
[Ноутбук вне LAN] ──WG──► [web-chat VM :8090]
                                ├──► LLM .41:8989
                                └──► SD .52:7860
```

При переходе на WG:

- Поднять интерфейс WG на сервере и клиентах; маршрутизировать подсеть `192.168.88.0/24` (или выделенную).
- `PUBLIC_BASE_URL` — адрес web-chat **внутри VPN** (тот же IP:порт, но доступный только после подключения WG).
- В MVP-коде **не нужны** отдельные ветки «если WG» — достаточно корректного `PUBLIC_BASE_URL` и bind на `0.0.0.0`.

### 15.2. systemd

```ini
[Unit]
Description=web-chat — монолит чат + MCP + агент
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/web-chat
EnvironmentFile=/root/web-chat/.env
ExecStart=/root/web-chat/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 15.3. PUBLIC_BASE_URL

Должен быть тем URL, который пользователь вводит в браузере, например:

`http://192.168.88.100:8090`

Иначе картинки в чате будут с битыми ссылками.

---

## 16. Seed-данные пресетов (полные тексты)

### 16.1. default (`is_default=true`)

```text
Ты полезный ассистент в приватном локальном чате.

Правила:
- Отвечай на языке пользователя, ясно и по делу.
- Если доступны инструменты — используй их вместо выдумывания фактов.
- Никогда не придумывай URL файлов, изображений или ссылок на ресурсы.
- Если не хватает данных — спроси уточнение.
```

### 16.2. image_gen

Совпадает с `app/db/seed.py` (`IMAGE_GEN_PROMPT`); при обновлении seed — `migrate.py` обновляет существующую БД.

```text
Ты помощник с доступом к генерации изображений через Stable Diffusion (инструмент generate_image).

Когда пользователь просит создать, нарисовать, сгенерировать, изменить картинку:
1. Сформируй детальный prompt (на английском предпочтительно для SD).
2. Вызови generate_image (для нескольких вариантов — параметр count за один вызов, до 10).
3. В текстовом ответе НЕ вставляй markdown-картинки (![...](url)) и не перечисляй URL —
   интерфейс сам покажет все сгенерированные изображения под сообщением.
4. Кратко прокомментируй результат текстом. Не придумывай ссылки. При ошибке — объясни простым языком.
```

### 16.3. document_analysis

```text
Ты помощник по анализу документов пользователя.

Правила:
- Текст документа может быть уже вставлен в сообщение пользователя.
- Если текста нет — вызови extract_text с attachment_id.
- Структурируй ответ: краткое резюме, ключевые пункты, при необходимости цитаты.
- Указывай имя файла, когда ссылаешься на документ.
- Не выдумывай содержимое, которого нет в тексте документа.
```

---

## 17. Дорожная карта v2

Не блокирует MVP:

- [ ] Inline-редактирование заголовка беседы
- [ ] Поиск по истории
- [ ] Экспорт беседы в Markdown
- [ ] PostgreSQL вместо SQLite
- [ ] Basic auth за reverse proxy
- [ ] `img2img` + инструкции denoising (см. image-gen TODO)
- [ ] Вкладка «Галерея»
- [ ] RAG / embeddings
- [ ] Поддержка нескольких пользователей

---

## 18. Зависимости (requirements)

```text
# Web
fastapi>=0.115
uvicorn[standard]>=0.32
python-multipart>=0.0.9
jinja2>=3.1

# Config & validation
pydantic-settings>=2.0

# Database
sqlalchemy[asyncio]>=2.0
aiosqlite>=0.20

# LLM & HTTP
openai>=1.50
httpx>=0.27

# MCP & images
fastmcp>=3.0
pillow>=10.0
requests>=2.31          # опционально на этапе 4, затем httpx

# Documents
pymupdf>=1.24
python-docx>=1.1

# Utils
python-dotenv>=1.0

# Dev
pytest>=8.0
pytest-asyncio>=0.24
ruff>=0.6
```

Опционально: `pytesseract` + системный `tesseract-ocr` для OCR сканов.

---

## 19. Критерий готовности MVP

MVP считается готовым после завершения **этапов 1–8** и выполнения всех пунктов:

| # | Критерий | Статус (2026-05-15) |
|---|----------|---------------------|
| 1 | UI в LAN (`http://<хост>:8090`) | ✅ |
| 2 | ≥2 беседы, переключение | ✅ |
| 3 | Стриминг текста от LLM | ✅ |
| 4 | «Нарисуй …» → tool → картинки в чате (сетка, SD .52) | ✅ |
| 5 | PDF в ответе | ✅ |
| 6 | Default preset на новую беседу | ✅ |
| 7 | F5 → история из REST | ✅ |
| 8 | Нет необработанных исключений в штатных сценариях | ⚠️ проверять на стенде |

**Итог:** критерии MVP **1–7 выполнены**; пункт 8 — периодический ручной прогон. Формально этап 8 закрыт по функционалу; этапы 9–10 — полировка и эксплуатация.

---

## Журнал прогресса

| Этап | Статус | Дата | Примечание |
|------|--------|------|------------|
| 1 | [x] | 2026-05 | Каркас, config, health (+ LLM/SD) |
| 2 | [x] | 2026-05 | SQLite, CRUD, seed, migrate |
| 3 | [x] | 2026-05 | Upload; image → MediaAsset |
| 4 | [x] | 2026-05 | MCP :8091, SD, ingest, `count` |
| 5 | [x] | 2026-05 | Agent, ToolExecutor, commit до tools |
| 6 | [x] | 2026-05 | extract_text, PDF/DOCX |
| 7 | [x] | 2026-05 | WS, history, regenerate, message actions |
| 8 | [x] | 2026-05 | UI: chat, lightbox, sidebar-settings |
| 9 | [~] | | Тема, error banner, status; нет font/model UI |
| 10 | [~] | | pytest 40, health, WAL; нет retention cron |
| 11 | [ ] | | img2img, gallery — не начато |

---

*Документ является живым гайдлайном. При изменении архитектуры — обновлять этот файл в том же PR/коммите, что и код.*
