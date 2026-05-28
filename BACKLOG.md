# BACKLOG — web-chat

> Источники: сверка [audit.md](audit.md) с [HANDBOOK.md](HANDBOOK.md) (2026-05-25); **аудит кода 2026-05-28** (`app/`, `static/js/`, `scripts/`, `data/`); **аудит CSS 2026-05-28** (`static/css/`).  
> Принципы отбора: [§0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки) (LAN, один оператор, UX > security theater).  
> Инциденты и эксплуатация: [docs/RUNBOOK.md](docs/RUNBOOK.md). План в handbook: [§22](HANDBOOK.md#22-планируемые-действия).

**Рекомендуемый порядок:** P0-S* → P3.1+P3.4 → P3.3/P3.5/P3.2 → P5.1/P5.2 → P6.1/P6.3 → **P7.1** → P7.3 → P7.2 → остальное по боли.

**CSS:** правки только инкрементально; один компонент/страница за PR; smoke: chat (desktop/mobile), gallery, login, macros, `/health`, light/dark, `prefers-reduced-motion`. **Не в одном PR:** split `chat.css` + health palette + pre colors.

---

## Результат сверки audit ↔ HANDBOOK

| Вердикт | Пункты |
|---------|--------|
| **В BACKLOG (ниже)** | P0 безопасность; P3 runtime; P4 perf; P5 JS; P6 ops; **P7 CSS / design system**; короткоживущие сессии БД, TurnContext, DRY WS-ошибок, busy-lock, settle, per-tool timeout, batch tools |
| **Уже в проекте / handbook** | `requests` + `HeavyJobQueue` (§12.4), `SqlAlchemyUnitOfWork`, vision `llm_data`, resume/F5, WS sweeper, structured logs, SSRF/trusted internal, fingerprint `loadMessages`, `tests/safety.py`, **`tokens.css`** + alias-слой в `chat.css`, `prefers-reduced-motion`, `.btn-*` / `.ui-card` |
| **Отложить без замера** | `BE-SD-HTTPX` — только после профилирования очереди jobs (§12.4) |
| **Не планировать (против §0.5 / SECURITY)** | Санитизация prompt injection, жёсткий `ALLOWED_LLM_URLS` по умолчанию, Prometheus как обязательный контур, Redis-кэш контекста, Service Worker |
| **Низкий приоритет / по запросу** | In-memory LRU кэш истории, Web Worker для markdown, preload lightbox, P5.8/P5.9, P7.6–P7.14, техдолг из аудита 2026-05-28 |

---

## Сильные стороны (сохранять)

- Слои `api/` → `services/` → `integrations/` → `db/`; `AppError` и коды WS-ошибок.
- Vision: `llm_data`, URL-first в контексте ([§0.1](HANDBOOK.md#01-что-строим)).
- Фронт: fingerprint + append-only `loadMessages`; `LightboxImage.detach()`; user-текст через `textContent` в macros.
- CSS: [tokens.css](static/css/tokens.css) (`--color-*`, radius, spacing, dark-theme); [chat.css](static/css/chat.css) alias `--bg`/`--primary` → tokens; login/gallery на тех же токенах; `focus-visible` + `--focus-ring`; глобальный `prefers-reduced-motion` (конец chat.css).
- Тесты: `tests/safety.py` блокирует production `DATABASE_URL`; 290+ passed.
- Ops: backup-скрипты с `set -euo pipefail`, rotation, manifest, pre-restore safety backup.

---

## P0 — безопасность (первым при `AUTH_ENABLED` / multi-user)

| ID | Задача | Обоснование | Ключевые файлы | Критерий готовности |
|----|--------|-------------|----------------|---------------------|
| **P0-S1** | **Guard в `link_to_message`** | `link_to_message` перепривязывает вложение без проверки `conversation_id`; в `sync_message_attachments` guard есть | `app/db/repositories.py` L598–611 vs L668–669 | Чужой `conversation_id` → `ValueError`; тест: WS с чужим `attachment_id` отклоняется |
| **P0-S2** | **Scope вложений в tools** | `extract_text` / `_load_init_image(attachment_id=…)` грузят по UUID без проверки беседы | `app/integrations/tool_executor.py` | `attachment_id` / `asset_id` только при `conversation_id == self._conversation_id` (или unassigned pending); тест cross-conversation read |
| **P0-S3** | **Политика WS URL → trusted internal** | Каждое WS-сообщение с `llm_base_url` / `sd_webui_url` вызывает `register_integration_urls` → расширение bypass `/media/asset/` | `app/integrations/runtime_config.py` L64, `app/security/trusted_internal.py` | Регистрация только env-хостов или после валидации (subnet/allowlist); произвольный URL из UI не расширяет trusted при `AUTH_ENABLED` |

---

## P3 — надёжность runtime (рекомендуется следующим спринтом)

Соответствует вектору «надёжность своего стенда» из §0.5. Не ломает state machine (черновики, WS, turn).

| ID | Задача | Обоснование | Ключевые файлы | Критерий готовности |
|----|--------|-------------|----------------|---------------------|
| **P3.1** | **Короткоживущие сессии БД в ходе агента** | `_run_turn_task` / `_run_regenerate_task` держат `AsyncSession` на весь ход (LLM + SD минуты) → пул PostgreSQL, locks SQLite | `app/api/websocket.py`, `app/services/agent_orchestrator.py`, `AssistantStreamDraft` | Сессия только на commit user-msg, flush черновика, финализацию; долгие `await` без соединения; `pytest -q`; txt2img >60 с на Postgres без ошибок пула |
| **P3.2** | **`TurnContext` (dataclass состояния хода)** | 15+ kwargs в `_run_completion_tool_calls`; дубли `run_conversation_turn` / `run_regenerate_turn` | `app/services/turn_context.py` (новый), `agent_orchestrator.py` | Ключевые методы принимают один `ctx`; без регрессий `test_tool_loop_*`, `test_turn_*` |
| **P3.3** | **DRY: общий wrapper ошибок WS-хода** | Дублирование `try/except` в `_run_turn_task` и `_run_regenerate_task` (~110 строк) | `app/api/websocket.py` | `_execute_and_handle_turn`; новый тип ошибки — в одном месте |
| **P3.4** | **Безопасный settle при мёртвой БД** | `_commit_or_settle_turn` на битой сессии может затереть `LLMError` → internal | `websocket.py`, `turn_recovery.py` | Падение settle → `logger.critical`, клиент видит исходный код; тест mock «session broken» |
| **P3.5** | **Атомарный захват хода (race busy)** | Окно между `is_busy()` и `set_active_task()` | `app/api/ws_manager.py`, `websocket.py` | `asyncio.Lock` per `conversation_id` или `try_acquire_turn()`; второй запрос → `ErrorCode.BUSY`; тест параллельных WS |
| **P3.6** | **Убрать coupling `services` → `api`** | Оркестратор/черновик/recovery импортируют `ws_manager` / `ws_events` | `agent_orchestrator.py`, `streaming_draft.py`, `turn_recovery.py` | Инжект `EventEmitter` + port; WS-типы только в `api/` |
| **P3.7** | **`ShutdownInProgress` в ToolExecutor** | Импорт есть, в SD-path не обрабатывается | `app/integrations/tool_executor.py` | Понятное сообщение пользователю вместо generic internal |
| **P3.8** | **Вынести `ToolAntiLoopExceeded`** | Runtime import из orchestrator в `conversation_tool_state` | `conversation_tool_state.py`, `errors.py` или `turn_exceptions.py` | Без circular import; общий модуль исключений хода |

---

## P4 — производительность и качество (по необходимости)

| ID | Задача | Обоснование | Примечание |
|----|--------|-------------|------------|
| **P4.1** | **Per-tool `timeout_seconds` в `TOOL_DEFINITIONS`** | Глобальные `REQUEST_TIMEOUT` / `MCP_TIMEOUT` не отражают SD vs `extract_text` | Дефолт = текущие settings |
| **P4.2** | **Параллельные tool_calls с семафором SD** | Несколько `generate_image` в раунде — последовательно | `asyncio.gather` + `Semaphore(1)` для SD; `extract_text` параллельно |
| **P4.3** | **Lazy vision в сборке контекста** | `llm_data` кэшируется; URL-first без BLOB в Python | Сверить §0.1; замер до/после |
| **P4.4** | **Расширить integration/load тесты** | §22: WS reconnect, concurrent WS | img2img/upscale; `@pytest.mark.load` |
| **P4.5** | **Расширенный `/api/health`** | `disk_free_mb`, `generated_count` для ops | По запросу оператора |
| **P4.6** | **Vision exists без BLOB** | `filter_unreachable_image_parts` вызывает `get_bytes()` каждый раунд LLM | `exists` / `get_by_id` без загрузки BLOB; связано с P4.3 |
| **P4.7** | **`get_gallery` без nested asyncio** | `asyncio.run` внутри `heavy_job_queue.run_sync` | `app/integrations/sd_tools.py` L622–644; async helper или sync DB API |

---

## P5 — фронтенд (`static/`)

UX важен (§0.5); XSS в assistant markdown — риск при shared LAN.

| ID | Задача | Приоритет | Ключевые файлы | Критерий готовности |
|----|--------|-----------|----------------|---------------------|
| **P5.1** | **Debounce / инкрементальный рендер стрима** | High (perf) | `static/js/chat.js` `_renderStreamTextToBubble`, `onTextDelta` | Не вызывать `formatMarkdown` на весь body на каждый `text_delta`; debounce 50–100 ms или plain text до конца стрима |
| **P5.2** | **Усилить санитизацию markdown** | Critical | `static/js/markdown.js` | DOMPurify (или AST) + allowlist URL (`/media/`); закрыть обход regex `sanitizeHtml` |
| **P5.3** | **Единый delegated click для картинок** | High | `chat.js` `_bindImageClicks` vs `_bindMessageImageActions` | Один listener на `#chat-messages`; нет стека handlers при re-render |
| **P5.4** | **Разбить `chat.js` (~5.6k строк)** | High | `static/js/chat.js` | Модули messages/conversations/settings/lightbox; тонкий `ChatApp`; предшествует крупному CSS-split (**P7.6**) |
| **P5.5** | **Общие `escapeHtml` / `escapeAttr`** | Medium | `chat.js`, `gallery.js`, `prompt-macros.js`, `macros-page.js` | `static/js/dom-utils.js`; полное attribute-escaping |
| **P5.6** | **Focus trap в lightbox** | Medium (a11y) | `chat.js` `openLightbox`, templates | Focus in dialog; `aria-hidden` на shell; restore focus on close |
| **P5.7** | **`BaseReconnectingSocket`** | Medium | `chat-ws.js`, `system-events.js` | Общий ping/reconnect; согласовать max attempts |
| **P5.8** | **Lifecycle composer** | Low | `chat-composer.js` | `disconnect` для document listeners и `ResizeObserver` |
| **P5.9** | **Объединить lightbox chat/gallery** | Low | `chat.js`, `gallery.js` | Shared controller + `LightboxImage.load` |

---

## P6 — эксплуатация (`scripts/`, `data/`)

| ID | Задача | Приоритет | Ключевые файлы | Критерий готовности |
|----|--------|-----------|----------------|---------------------|
| **P6.1** | **Confirm до `tar -xzf`** | High | `scripts/restore-database.sh` L106 vs L141 | Подтверждение (или `--yes`) **до** распаковки архива |
| **P6.2** | **Fail-fast PostgreSQL restore** | High | `restore-database.sh` L189–196 | Убрать `psql \|\| true` / `dropdb \|\| true` где безопасно; явные exit codes |
| **P6.3** | **Удалить/ротировать `data/.pg_migrate_secret`** | Critical (ops) | `data/.pg_migrate_secret` | Ротировать пароль; удалить файл (не используется app); не коммитить |
| **P6.4** | **Legacy SQLite ~2.1 GB в бэкапах** | Medium | `scripts/lib/backup-database-core.sh`, `data/db/README.md` | `WEB_CHAT_BACKUP_LEGACY_SQLITE=0` по умолчанию в prod |
| **P6.5** | **Ротация site-backup** | Medium | `scripts/backup-all.sh` | `backup_rotate` для `WEB_CHAT_SITE_BACKUP_DIR` или cron cleanup |
| **P6.6** | **Post-restore verify** | Medium | `restore-database.sh` | `verify_migration` / health после restore; в выводе скрипта |
| **P6.7** | **Smoke-тест shell backup** | Low | `tests/` | Dry-run backup/restore с temp SQLite или чеклист в CI |

`data/uploads/` (~70 PDF) — в `.gitignore`; ops: бэкап uploads при `WEB_CHAT_BACKUP_UPLOADS=1`.

---

## P7 — CSS и единообразие дизайна (`static/css/`)

Архитектура: `tokens.css` → `chat.css` (~6k строк: chat + gallery + login + macros); `health.css` — отдельно, без `tokens.css`. Принцип: **не ломать работающий UI** — мелкие PR, ручной smoke после каждого.

### Порядок CSS-правок (без регрессий)

1. **P7.1** — `pre` в light theme (визуальный баг, низкий риск).
2. **P7.3** — токен градиента primary (косметика, много мест).
3. **P7.2** — health на design system (отдельная страница).
4. **P7.4, P7.5, P7.10, P7.11** — по мере правок соседних блоков.
5. **P7.6** — split `chat.css` только после **P5.4**.

**Критерий «не сломали»:** composer, sidebar mobile, lightbox, dark toggle на chat/login/gallery; macros table; `/health` cards; `prefers-reduced-motion: reduce`.

### P7 — критичное и high

| ID | Задача | Приоритет | Ключевые файлы | Критерий готовности |
|----|--------|-----------|----------------|---------------------|
| **P7.1** | **`pre` в assistant — theme-aware** | Critical (visual) | `chat.css` L3147–3160 | Light: светлый фон/текст; dark: текущий GitHub-style; CSS-переменные в `:root` / `body.dark-theme`; markdown JS не трогать |
| **P7.2** | **Health на design system** | High | `health.css`, `health.html` | Подключить `tokens.css`; layout остаётся в `health.css`; semantic → `var(--color-success)` и т.д.; опционально light/dark как у чата |
| **P7.3** | **Токен конца градиента primary** | High | `chat.css` (~6× `#4285f4`) | `--gradient-primary-end` в `:root` или `color-mix` от `--primary` |
| **P7.4** | **Использовать `--space-*`** | High (постепенно) | `chat.css` (сейчас ~11 вхождений vs сотни `px`) | Новый CSS — только токены; замена в composer/modal при соседних правках |
| **P7.5** | **Объединить дубли `@media (max-width: 768px)`** | High | `chat.css` L4442, L4477 | Один mobile-блок lightbox + shell |

### P7 — medium

| ID | Задача | Обоснование | Примечание |
|----|--------|-------------|------------|
| **P7.6** | **Split `chat.css`** | Монолит chat+gallery+login+macros | После **P5.4**; файлы `chat-layout.css`, `chat-messages.css`, `gallery.css`, `login.css` |
| **P7.7** | **Шкала z-index** | Значения 2…2000 без документации | `--z-sidebar`, `--z-modal`, `--z-lightbox` в `tokens.css`; миграция по слою |
| **P7.8** | **Зафиксировать breakpoints** | 420 / 640 / 768 / 600 (health) | Комментарий в CSS или handbook: sidebar 640, mobile shell 768, login 420 |
| **P7.9** | **Именование `--radius-lg`** | В chat `:root` `--radius-lg` = `var(--radius-xl)` при tokens `--radius-lg: 18px` | Alias `--radius-bubble` или комментарий; не путать с tokens |
| **P7.10** | **Алиас `--ease`** | tokens `--ease-ui` vs chat `--ease` | `--ease: var(--ease-ui)` в chat `:root` |
| **P7.11** | **Scrollbar tokens** | Hex только в chat `:root` L38–59 | `--scrollbar-*` в `tokens.css` для light/dark |

### P7 — low

| ID | Задача | Примечание |
|----|--------|------------|
| **P7.12** | `!important` (10×) | В основном `.hidden`, reduced-motion, иконки темы; не удалять без теста |
| **P7.13** | `theme-color` в health.html | Всегда `#0b1018`; синхронизировать после P7.2 |
| **P7.14** | Локальные `@media (prefers-reduced-motion)` | Глобальный reset в конце chat.css покрывает большинство; точечно — по жалобам |

---

## Отложено (уже описано в HANDBOOK)

| ID | Задача | Когда брать |
|----|--------|-------------|
| **BE-SD-HTTPX** | Миграция SD HTTP: `requests` → `httpx.AsyncClient` | После замера: `HeavyJobQueue` depth, txt2img, workers ([§12.4](HANDBOOK.md#124-sd-webui-http-клиент-be-sd-httpx)) |
| **P2.6** | Redis/NATS event bus | Несколько инстансов uvicorn |
| **P2.7** | Semantic memory, agent planning | Future |
| **RAG v2** | Глобальный корпус, cron reindex | После пилота P2.3 |

---

## Низкий приоритет (только при измеримой боли)

| Задача | Условие / файлы |
|--------|-----------------|
| In-memory LRU кэш `build_conversation_llm_context` | >300 ms в логах при типичной истории |
| Retention cleanup через `heavy_job_queue.run_sync` | Лаг event loop при больших `data/generated/` |
| Frontend: Web Worker для markdown | Фризы UI при длинном стриме |
| Frontend: preload соседних картинок в lightbox | UX-полировка |
| Унифицировать импорты `async_session_factory` | `session.py` vs `db_session` ([§14.4](HANDBOOK.md#144-парадигма-pytest-обязательно-для-новых-тестов)) |
| Мёртвый `AssistantStreamDraft.discard()` | `streaming_draft.py` L248 |
| Unused import `heavy_job_queue` в websocket | `websocket.py` L30 |
| Расширить `SqlAlchemyUnitOfWork` или убрать из hot path | `db/uow.py` — сейчас только `media_registry.py` |
| `ToolExecutor` → `AttachmentService._repo` (private) | `tool_executor.py` L145 |
| Instance-scoped cache в `LLMClient` | `llm_client.py` global `_MODEL_CACHE` |
| `formatApiErrorDetail` в `chat.js` `api()` | Как в `chat-composer.js` |
| Убрать debug `console.debug` img2img | `chat-ws.js` |
| `pkill -f uvicorn` в restore | Предпочитать `systemctl stop web-chat` |
| `PGPASSWORD` в process list при backup | `.pgpass` mode 0600 |
| Concurrent backup + restore | `flock` или документация «один оператор» |
| Градиенты primary / `--space-*` / scrollbar | См. **P7.3, P7.4, P7.11** — только при правках соседнего UI |
| z-index шкала, split chat.css | **P7.6, P7.7** — после P5.4 |
| `!important` в chat.css | **P7.12** |

---

## Не включать (audit отклонён по HANDBOOK)

| Пункт audit | Причина |
|-------------|---------|
| UnitOfWork как в audit §1.2 | Уже есть `SqlAlchemyUnitOfWork` + Protocol в `app/db/uow.py`; **расширить использование**, не дублировать |
| Санитизация prompt injection (§8 audit) | [§0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки), [SECURITY.md](SECURITY.md) — вне фокуса |
| `ALLOWED_LLM_URLS` whitelist (§4 audit) | Конфликтует с runtime override из UI; SSRF/trusted internal уже есть. Опционально — флаг при `AUTH_ENABLED` |
| Prometheus /metrics (§6 audit) | Избыточно для одного оператора; JSON-логи + `log_turn_context` |
| WS heartbeat + внешний cancel API (§7 audit) | F5/resume, sweeper, cancel через WS `cancel` |
| Service Worker оффлайн (§6 FE audit) | Вне scope v1 ([§0.3](HANDBOOK.md#03-не-цели-версии-1-v1)) |
| Property-based / нагрузочные тесты как обязательные | По запросу; 290+ passed достаточно для LAN |

---

## P1 — эксплуатация (из §22 handbook, без изменений)

| Задача | Когда |
|--------|-------|
| Basic Auth / HTTPS ([nginx template](deploy/nginx-web-chat.conf.template)) | Хост доступен не только вам |
| Чеклист §7 на стенде | Пинг LLM/SD, `PUBLIC_BASE_URL`, `systemctl is-enabled` |

---

*При закрытии задачи — обновить журнал в [HANDBOOK.md](HANDBOOK.md#журнал-прогресса) и при необходимости §12.4 / §22.*
