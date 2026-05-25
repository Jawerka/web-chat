# BACKLOG — web-chat

> Источник: сверка [audit.md](audit.md) с [HANDBOOK.md](HANDBOOK.md) (2026-05-25).  
> Принципы отбора: [§0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки) (LAN, один оператор, UX > security theater).  
> Инциденты и эксплуатация: [docs/RUNBOOK.md](docs/RUNBOOK.md). План в handbook: [§22](HANDBOOK.md#22-планируемые-действия).

---

## Результат сверки audit ↔ HANDBOOK

| Вердикт | Пункты audit |
|---------|----------------|
| **В BACKLOG (ниже)** | Короткоживущие сессии БД, TurnContext, DRY WS-ошибок, атомарный busy-lock, безопасный settle, per-tool timeout, batch tools + семафор SD, расширение тестов, опциональный health |
| **Уже в проекте / handbook** | `requests` + `HeavyJobQueue` (§12.4), `SqlAlchemyUnitOfWork` (`app/db/uow.py`), vision `llm_data`, resume/F5, WS sweeper, structured logs (`log_context.py`), SSRF/trusted internal |
| **Отложить без замера** | `BE-SD-HTTPX` — только после профилирования очереди jobs (§12.4) |
| **Не планировать (против §0.5 / SECURITY)** | Санитизация prompt injection, жёсткий `ALLOWED_LLM_URLS` по умолчанию, Prometheus как обязательный контур, Redis-кэш контекста, Service Worker |
| **Низкий приоритет / по запросу** | In-memory LRU кэш истории, Web Worker для markdown, preload lightbox, cleanup в thread pool |

---

## P3 — надёжность runtime (рекомендуется следующим спринтом)

Соответствует вектору «надёжность своего стенда» из §0.5 и детальному аудиту (§2 audit, этапы 1–4). Не ломает state machine (черновики, WS, turn).

| ID | Задача | Обоснование | Ключевые файлы | Критерий готовности |
|----|--------|-------------|----------------|---------------------|
| **P3.1** | **Короткоживущие сессии БД в ходе агента** | Сейчас `_run_turn_task` / `_run_regenerate_task` держат `AsyncSession` открытой на весь ход (LLM + SD минуты) → риск исчерпания пула PostgreSQL, `connection closed`, locks SQLite | `app/api/websocket.py`, `app/services/agent_orchestrator.py`, `AssistantStreamDraft` | Сессия открывается только на commit user-msg, flush черновика, финализацию; долгие `await` (LLM/SD) без удержания соединения; `pytest -q` зелёный; ручной txt2img >60 с на Postgres без ошибок пула |
| **P3.2** | **`TurnContext` (dataclass состояния хода)** | Методы оркестратора с 15+ kwargs (`_run_completion_tool_calls` и др.) — сложно сопровождать и тестировать | `app/services/turn_context.py` (новый), `agent_orchestrator.py` | Сигнатуры ключевых методов принимают один `ctx`; поведение без регрессий по `test_tool_loop_*`, `test_turn_*` |
| **P3.3** | **DRY: общий wrapper ошибок WS-хода** | Дублирование `try/except` в `_run_turn_task` и `_run_regenerate_task` (~50 строк) | `app/api/websocket.py` | Одна функция `_execute_and_handle_turn`; новый тип ошибки добавляется в одном месте |
| **P3.4** | **Безопасный settle при мёртвой БД** | В `except` вызов `_commit_or_settle_turn` на битой сессии может затереть исходную ошибку (`LLMError` → internal) | `websocket.py`, `turn_recovery.py` | При падении settle — `logger.critical`, клиент получает код исходной ошибки; тест на mock «session broken» |
| **P3.5** | **Атомарный захват хода (race busy)** | Между `is_busy()` и `set_active_task()` окно для двух `user_message` подряд | `app/api/ws_manager.py`, `websocket.py` | `asyncio.Lock` per `conversation_id` или `try_acquire_turn()`; второй запрос → `ErrorCode.BUSY`; тест на параллельные WS-сообщения |

---

## P4 — производительность и качество (по необходимости)

| ID | Задача | Обоснование | Примечание |
|----|--------|-------------|------------|
| **P4.1** | **Per-tool `timeout_seconds` в `TOOL_DEFINITIONS`** | Глобальные `REQUEST_TIMEOUT` / `MCP_TIMEOUT` не отражают разницу SD (минуты) vs `extract_text` (секунды) | Не ломает UX; дефолт = текущие settings |
| **P4.2** | **Параллельные tool_calls с семафором SD** | Несколько `generate_image` в одном раунде сейчас последовательно | `asyncio.gather` + `Semaphore(1)` только для SD-инструментов; `extract_text` параллельно |
| **P4.3** | **Lazy vision в сборке контекста** | `llm_data` уже кэшируется (журнал 2026-05-24); при URL-first можно не грузить BLOB в Python, если модель принимает `/media/asset/{id}/llm` | Сверить с §0.1 «в контекст не base64»; замер до/после |
| **P4.4** | **Расширить integration/load тесты** | §22 handbook: WS reconnect под нагрузкой, concurrent WS | img2img/upscale edge cases; опционально `@pytest.mark.load` |
| **P4.5** | **Расширенный `/api/health`** | §22: `disk_free_mb`, `generated_count` — для ops, не для LAN по умолчанию | По запросу оператора |

---

## Отложено (уже описано в HANDBOOK)

| ID | Задача | Когда брать |
|----|--------|-------------|
| **BE-SD-HTTPX** | Миграция SD HTTP: `requests` → `httpx.AsyncClient` | После замера: `HeavyJobQueue` depth, время txt2img, исчерпание workers ([§12.4](HANDBOOK.md#124-sd-webui-http-клиент-be-sd-httpx)) |
| **P2.6** | Redis/NATS event bus | Несколько инстансов uvicorn |
| **P2.7** | Semantic memory, agent planning | Future |
| **RAG v2** | Глобальный корпус, cron reindex | После пилота P2.3 |

---

## Низкий приоритет (только при измеримой боли)

| Задача | Условие |
|--------|---------|
| In-memory LRU кэш `build_conversation_llm_context` | Профилирование: стабильно >300 ms при типичной истории (сейчас только warning в лог) |
| Retention cleanup через `heavy_job_queue.run_sync` | Лаг event loop при больших `data/generated/` |
| Frontend: Web Worker для markdown | Фризы UI при длинном стриме с таблицами/кодом |
| Frontend: preload соседних картинок в lightbox | UX-полировка |
| Унифицировать импорты `async_session_factory` | Техдолг: в части integrations прямой импорт из `session.py` вместо `db_session` ([§14.4](HANDBOOK.md#144-парадигма-pytest-обязательно-для-новых-тестов)) |

---

## Не включать (audit отклонён по HANDBOOK)

| Пункт audit | Причина |
|-------------|---------|
| UnitOfWork как в audit §1.2 | Уже есть `SqlAlchemyUnitOfWork` + Protocol в `app/db/uow.py` (P1.1); нужно **расширить использование**, а не дублировать |
| Санитизация prompt injection (§8 audit) | [§0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки), [SECURITY.md](SECURITY.md) — сознательно вне фокуса |
| `ALLOWED_LLM_URLS` whitelist (§4 audit) | Конфликтует с runtime override из UI; SSRF/trusted internal уже есть (P0.5). Опционально — только за флагом при `AUTH_ENABLED` и общей сети |
| Prometheus /metrics (§6 audit) | Избыточно для одного оператора; частично закрыто JSON-логами + `log_turn_context` |
| WS heartbeat + внешний cancel API (§7 audit) | F5/resume и фоновая генерация уже исправлены; sweeper в `ws_manager`; cancel через WS `cancel` |
| Service Worker оффлайн (§6 FE audit) | Вне scope v1 ([§0.3](HANDBOOK.md#03-не-цели-версии-1-v1)) |
| Property-based / нагрузочные тесты как обязательные | Только по запросу; база 290+ passed достаточна для LAN |

---

## P1 — эксплуатация (из §22 handbook, без изменений)

| Задача | Когда |
|--------|-------|
| Basic Auth / HTTPS ([nginx template](deploy/nginx-web-chat.conf.template)) | Хост доступен не только вам |
| Чеклист §7 на стенде | Пинг LLM/SD, `PUBLIC_BASE_URL`, `systemctl is-enabled` |

---

*При закрытии задачи — обновить журнал в [HANDBOOK.md](HANDBOOK.md#журнал-прогресса) и при необходимости §12.4 / §22.*
