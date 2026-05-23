# TODO-2 — приоритизированный план доработок

> **Источники:** сводный аудит [`audit.md`](audit.md), направление и ограничения [`TODO.md`](TODO.md).  
> **Статус кодовой базы (2026-05-23):** MVP закрыт; **162** автотестов (`pytest -q`). **P0 закрыт**; в работе **P1** (tool anti-loop, SQLite metrics). Журнал в [TODO.md §21](TODO.md#21-стабилизация-todo-2-2026-05-23).

---

## Направление проекта (не терять при приоритизации)

Из [`TODO.md`](TODO.md) — это «северная звезда», а не бэклог на удаление:

| Принцип | Что значит для плана |
|---------|----------------------|
| **LAN-first монолит** | Один FastAPI-процесс, SQLite, in-process tools — не дробить на микросервисы без веской причины |
| **URL, не base64** | LLM и UI работают через HTTP `/media/asset/{uuid}` и `/llm` |
| **Commit user-msg до SD/LLM** | Избегать `database is locked`; не откатывать user-сообщение при ошибке turn |
| **Стриминг + resume** | `AssistantStreamDraft`, WS `connected`, poll после F5 — дорабатывать, не ломать |
| **img2img: сервер владеет init** | Приоритет вложения user-сообщения над URL от модели ([§9.5](TODO.md#95-пайплайн-img2img-пресет-img2img)) |
| **v1 ≠ интернет** | Auth, rate limit, proxy — обязательны **до** публичного доступа ([§7](TODO.md#7-чеклист-перед-production), [§17](TODO.md#17-дорожная-карта-v2)) |
| **Стабилизация > feature growth** | Следующая волна — state lifecycle и эксплуатация, а не новые большие фичи |

**Стратегический вывод аудита:** проект вышел из «умного MVP»; главный риск — накопление неявного состояния (WS, черновики, вложения, regenerate), а не отсутствие UI.

---

## Легенда приоритетов

| Уровень | Когда делать | Критерий «готово» |
|---------|--------------|-------------------|
| **P0** | Сразу, до выхода за доверенную LAN/VPN | Без этого нельзя считать контур безопасным или предсказуемым |
| **P1** | Ближайший спринт стабилизации | Снимает гонки, нагрузку на SQLite, хрупкость orchestration |
| **P2** | После P0–P1 | Масштаб, multi-user, Postgres — из roadmap v2 |
| **Фичи** | Параллельно P1, если не ломают state | Продуктовые запросы из старого TODO-2 |

---

## Уже есть в коде (не планировать заново)

- SQLite WAL, `busy_timeout`, retry `OperationalError` — `app/db/sqlite.py`, `run_write`
- Debounced flush стрима (~350 ms) — `AssistantStreamDraft`
- Resume после F5, generation-status, dedupe картинок в UI
- `IMAGE_URL_RE` в `tool_executor.py` — корректный паттерн (аудит с багом устарел)
- img2img: server-first init, без `get_gallery` в пресете, denoise 0.54
- Vision `/media/asset/{id}/llm`, dual `PUBLIC_BASE_URL` (LAN/VPN) — `app/public_url.py`
- Базовая санитизация markdown — `static/js/markdown.js` (`sanitizeHtml`)
- Удаление **одного** элемента галереи + purge ссылок в сообщениях — `DELETE /api/gallery/...`
- Deploy: `install.sh`, backup, retention timer, health dashboard
- Быстрые промпты `@alias` — CRUD, раскрытие на сервере (без «скилла» и без embeddings)

---

# P0 — критическая стабилизация и безопасность

> Соответствует [TODO.md §7](TODO.md#7-чеклист-перед-production) и [§17](TODO.md#17-дорожная-карта-v2) (auth, rate limit).  
> Деплой только в LAN / WireGuard / Tailscale до закрытия P0.

## P0.1 — Контур доступа (auth + proxy)

**Проблема:** любой, кто знает `conversation_id`, может подключиться к WS; нет проверки `Origin` (CSWSH).

**Задачи:**

- [x] Документировать и шаблонизировать reverse proxy (nginx / Caddy / Traefik): HTTPS, Basic Auth или OAuth2-proxy — [`deploy/nginx-web-chat.conf.template`](deploy/nginx-web-chat.conf.template), [DEPLOY.md §11](deploy/DEPLOY.md#11-reverse-proxy-nginx)
- [x] Минимум в приложении: API key / shared secret для REST и WS (заголовок или query при upgrade)
- [x] Валидация `Origin` / `Host` на WebSocket; список доверенных origins в `.env`
- [x] Ограничить доверие к `X-Forwarded-*` — `TRUSTED_PROXY_IPS` ([`access.py`](app/security/access.py))
- [x] Добавить `SECURITY.md`: что допустимо в LAN, что запрещено (публичный порт, открытый WS)

**Критерии готовности:** неавторизованный WS → отказ; внешний доступ только через proxy + auth; чеклист §7 обновлён.

**Связь с TODO.md:** [§17 Basic auth](TODO.md#17-дорожная-карта-v2), [§1.12](TODO.md#112-безопасность).

---

## P0.2 — Rate limiting

**Проблема:** в [TODO.md §20.5](TODO.md#205-не-реализовано--отложено) явно: *«нет в коде»*; флуд upload / generate / WS перегружает SD и LLM.

**Задачи:**

- [x] In-memory лимитер (первая итерация): [`app/security/rate_limit.py`](app/security/rate_limit.py)
- [x] Лимиты на: `POST /api/upload`, WS `user_message`, `POST /api/conversations`, `DELETE /api/gallery/all`
- [x] Ответ `429` + код `rate_limit_error` в WS/REST
- [x] Настройки в `.env`: `RATE_LIMIT_REQUESTS`, `RATE_LIMIT_WINDOW_SEC`, `RATE_LIMIT_ENABLED`

**Критерии готовности:** воспроизводимый тест «N запросов подряд → 429»; документация в `DEPLOY.md`.

---

## P0.3 — Жизненный цикл WebSocket (`ConnectionManager`)

**Проблема:** [`ws_manager.py`](app/api/ws_manager.py) — `disconnect()` чистит только `_connections`; `_cancel_events`, `_active_tasks`, `_streaming_messages` могут оставаться после обрыва.

**Задачи:**

- [x] Финализатор при `disconnect` последнего сокета беседы: очистка cancel/task/streaming (если turn не активен)
- [x] Гарантированный `clear_active_task` в callback turn-задачи
- [x] Периодический sweeper «зомби» (idle timeout, task.done() но не cleared)
- [x] Ввести `ConversationSessionState` (одна структура вместо четырёх словарей)

**Критерии готовности:** тест reconnect × N без роста `_active_tasks`; нет «вечного» `is_busy` после обрыва вкладки.

---

## P0.4 — Согласованность стрима, БД и UI при ошибке/отмене

**Проблема:** при `TurnCancelled` / `LLMError` / `ToolLoopExceeded` в [`websocket.py`](app/api/websocket.py) — `rollback()` + иногда `discard()` черновика; пользователь уже видел `text_delta` / `image`.

**Задачи:**

- [ ] Явная модель статусов turn: `queued → streaming → tool_running → partial → completed | cancelled | failed` (частично: `turn_status` в content_json)
- [x] При ошибке: **не** удалять черновик с контентом — [`turn_recovery.py`](app/services/turn_recovery.py)
- [x] User-message всегда остаётся закоммиченным — `tests/test_turn_user_commit.py`
- [x] WS `error` с `code`; commit вместо rollback для LLM/tool/cancel/internal

**Критерии готовности:** сценарий «обрыв LLM на середине стрима → F5 → текст/картинки на месте»; тест `test_tool_limit_draft` и аналоги зелёные.

---

## P0.5 — Trust boundary для URL (SSRF / подмена)

**Проблема:** `PUBLIC_BASE_URL` из Host/Forwarded влияет на ссылки для браузера; злоупотребление конфигом или заголовками — риск неверных/вредоносных URL для vision.

**Задачи:**

- [x] Валидатор `public_base_url` / `public_base_url_vpn` в `Settings` (схема, loopback/metadata)
- [x] Аудит всех точек, где LLM получает внешний URL; для vision — только `for_llm=True` (LAN base) — тест `test_for_llm_ignores_vpn_host`
- [x] Жёстче `is_trusted_media_url` для img2img/upscale — `tests/test_security_urls.py`, `test_stage11_sd.py`

**Критерии готовности:** security-тесты на отклонение `http://169.254.169.254/...` и произвольного Host.

---

# P1 — архитектурное укрепление (ближайший спринт)

## P1.1 — SQLite под нагрузкой (дополнить имеющееся)

**Уже есть:** WAL, retry, debounce flush. **Добавить:**

- [ ] Тесты конкурентных записей (2+ вкладки, одна беседа)
- [x] Метрика/лог при срабатывании retry `database is locked` — `sqlite_busy_retries_total`, health `/api/health`
- [ ] Опционально: батч по размеру буфера (2 KB), не только по таймеру — снизить write amplification на длинных ответах
- [ ] Подготовка DAO-слоя под Postgres ([TODO.md §17](TODO.md#17-дорожная-карта-v2)) — без миграции данных пока

---

## P1.2 — Изоляция тяжёлых операций

**Проблема:** SD, extract PDF, upscale в одном event loop — риск лагов WS.

**Задачи:**

- [ ] Единая очередь job’ов (`asyncio.Queue` + worker tasks) для: txt2img, img2img, upscale, extract_text (большие файлы)
- [ ] Cancellation token до/после каждого долгого шага (в т.ч. SD HTTP)
- [ ] WS только для статуса и стрима; не блокировать `_websocket_chat_loop` на `receive_json` — вынести обработку в task + queue (аудит: «блокирующий цикл»)

**Критерии готовности:** при зависшем SD стрим текста на **другой** вкладке не замирает > N секунд.

---

## P1.3 — Единый event bus вместо polling

**Проблема:** poll generation-status, logs, gallery каждые 2–5 с — лишние чтения БД.

**Задачи:**

- [ ] Расширить WS-события: `generation_update`, `gallery_update`, `logs_append` (постепенно вытеснить poll)
- [ ] Сохранить REST fallback для F5 и offline
- [ ] Согласовать с `ConnectionManager` (broadcast по `conversation_id` или global channel)

**Связь с TODO.md:** [§10.7 resume](TODO.md#107-resume-генерации-после-f5) — не сломать.

---

## P1.4 — Tool orchestration (anti-loop)

**Проблема:** `MAX_TOOL_ROUNDS=10` — предохранитель, не план; риск повторного img2img/upscale.

**Задачи:**

- [x] `ConversationToolState`: хеши вызовов, лимит SD-tools — [`conversation_tool_state.py`](app/services/conversation_tool_state.py)
- [x] Детект дубликата tool(args) в одном turn
- [x] Проверка `cancel_event` перед каждым tool (`before_tool`)
- [x] Валидация `attachment_ids` с WS-ошибкой вместо silent `pass` ([websocket.py](app/api/websocket.py))

**Критерии готовности:** тест «модель зовёт img2img 5 раз подряд → остановка с понятной ошибкой».

---

## P1.5 — Upload и медиа

**Задачи:**

- [ ] Magic bytes + попытка decode изображения (Pillow), лимит размеров/пикселей
- [ ] Защита PDF/DOCX: лимит страниц/символов, таймаут extract в thread
- [ ] Единый media registry (метаданные в БД = источник правды; disk — storage) — задел под P2

**Связь с TODO.md:** [§1.11](TODO.md#111-обработка-вложений), `safe_filename` уже есть.

---

## P1.6 — Наблюдаемость и ошибки

- [ ] Структурированные логи (JSON опционально), correlation id на WS-сессию
- [ ] Канонические коды [`AppError`](audit.md) → [§13](TODO.md#13-обработка-ошибок)
- [ ] Health: свободное место `data/`, размер WAL, число активных WS-turn
- [ ] Разделить `except Exception` в WS: бизнес (warning) vs internal (error + log)

---

## P1.7 — Тесты и документация

- [x] Починить `tests/test_tool_limit_draft.py` (`streaming: None` в финальном сообщении)
- [x] Tool loop integration — `tests/test_tool_loop_integration.py`
- [ ] WS reconnect + resume; concurrent tabs; cancel mid-tool
- [x] Security: SSRF URL / trusted media — `tests/test_security_urls.py`
- [ ] Security: path traversal upload, XSS payload в markdown
- [x] Синхронизировать счётчик тестов: README / TODO.md / факт (`pytest -q`)
- [ ] Load smoke: 5–10 параллельных «лёгких» чатов без deadlock

---

# P2 — масштаб и roadmap v2 (после стабилизации)

| ID | Задача | Связь с TODO.md |
|----|--------|-----------------|
| P2.1 | PostgreSQL + миграции Alembic | [§17](TODO.md#17-дорожная-карта-v2) |
| P2.2 | Multi-user: изоляция бесед, quotas, роли | [§17](TODO.md#17-дорожная-карта-v2), [§0.3](TODO.md#03-не-цели-версии-1-v1) |
| P2.3 | RAG / embeddings (отдельно от «скилла» — см. Фичи) | [§17](TODO.md#17-дорожная-карта-v2) |
| P2.4 | Media registry: orphan cleanup, dedup, retention policies | [§20](TODO.md#20-доработки-после-mvp-итерации-разработки) |
| P2.5 | `localStorage` schema versioning + migrations на клиенте | audit: corruption при обновлениях UI |
| P2.6 | Redis / NATS event bus + horizontal scale | только при реальной потребности |
| P2.7 | Semantic memory, agent planning layer | future, не блокирует LAN |

---

# Продуктовые фичи (из прежнего TODO-2)

> Делать **после** или **вместе с** P0.3–P0.4, чтобы не усугубить рассинхрон состояния.

## Ф1 — Скил «полный доступ к @alias» в чате

**Запрос:** кнопка-переключатель в чате (по умолчанию **выкл**), даёт модели контекст по **всей** базе макросов, а не только подставленным `@alias`.

**Задачи:**

- [ ] UI: toggle в composer / toolbar, состояние в `sessionStorage` или settings
- [ ] Backend: флаг в WS `user_message` → режим `macro_context=full|selected`
- [ ] При `full`: ограниченный снимок каталога (лимит символов/число макросов), не безлимитный дамп БД
- [ ] Документация риска: раздувание контекста, утечка всех промптов в LLM

**Приоритет:** P1 (фича), зависит от P0.4 (предсказуемый turn).

---

## Ф2 — Embeddings для @alias (оценка и пилот)

**Вопрос из запроса:** при обычном использовании 1–2 alias (~0.1–1% базы) — **не** векторизировать всё целиком в hot path.

**Рекомендуемый подход:**

| Режим | Когда | Поведение |
|-------|-------|-----------|
| **Точечный** (default) | Пользователь ввёл `@alias` | Как сейчас: подстановка текста макроса |
| **Поиск по каталогу** | Включён скилл Ф1 или «умный выбор» | Top-K макросов по embedding запроса пользователя |
| **Полная индексация** | Offline / cron | Фоновая векторизация таблицы `PromptMacro`; не в критическом пути WS |

**Задачи:**

- [ ] Spike: локальная embedding-модель (LAN), хранение векторов (SQLite JSON / отдельная таблица / sqlite-vec)
- [ ] API: `GET /api/prompt-macros/search?q=` с semantic rank
- [ ] Лимит K (например 3–5) и max tokens в системный контекст
- [ ] Не дублировать [TODO.md §17 RAG](TODO.md#17-дорожная-карта-v2) по документам — только макросы

**Приоритет:** P2 / опционально P1 после Ф1.

---

## Ф3 — «Очистить всю галерею» с подтверждением

**Запрос:** кнопка на странице галереи, с confirm, удаляет **все** изображения.

**Сейчас:** удаление по одному (`DELETE /api/gallery/db/{id}`, `.../disk/{filename}`).

**Задачи:**

- [x] `DELETE /api/gallery/all` + `purge_messages=true|false`
- [x] UI: кнопка «Очистить галерею», confirm с числом изображений
- [x] Батч до `GALLERY_MAX_LIMIT` (1000)
- [x] Тест: `test_purge_all_gallery_empty`

**Приоритет:** P1 (фича), низкий риск для архитектуры.

---

# Вехи (milestones)

```text
M1 — Secure LAN          P0.* + Ф3 + документация деплоя
M2 — Stable runtime      P1.1–P1.4, P1.7, Ф1
M3 — Platform v2         P2.*, Ф2, Postgres, multi-user
```

**Правило из аудита:** до завершения **M2** не добавлять крупные user-facing фичи, кроме перечисленных в §Фичи и согласованных с state machine.

---

# Чеклист регрессии (после каждого крупного PR)

Скопировано и дополнено из [TODO.md §20.6](TODO.md#206-чеклист-регрессии-после-правок):

- [ ] SD → F5 → статус и сетка без дублей
- [ ] img2img regenerate → в логах `init взят из user-сообщения`
- [ ] `@@macro` → один `@` в UI
- [ ] `pytest -q` — все зелёные
- [ ] `PUBLIC_BASE_URL` / VPN URL в health совпадают с браузером
- [ ] После обрыва WS нет вечного «генерация…» (`is_busy`)

---

# Быстрая карта «аудит → пункт плана»

| Тема в audit.md | Пункт TODO-2 |
|-----------------|--------------|
| Нет auth / CSWSH | P0.1 |
| Нет rate limit | P0.2 |
| Утечка WS state | P0.3 |
| Rollback vs UI | P0.4 |
| SSRF / PUBLIC_BASE_URL | P0.5 |
| SQLite locks / частые записи | P1.1 (частично сделано) |
| SD блокирует loop | P1.2 |
| Polling | P1.3 |
| Tool loops | P1.4 |
| Upload security | P1.5 |
| Тесты / drift docs | P1.7 |
| Postgres / multi-user | P2 |
| Скилл @alias | Ф1 |
| Embeddings | Ф2 |
| Очистка галереи | Ф3 |

---

*При закрытии пункта — отмечать `[x]` и при необходимости переносить однострочную запись в [журнал TODO.md](TODO.md#журнал-прогресса).*
