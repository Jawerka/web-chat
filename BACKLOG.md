# BACKLOG — web-chat

> Источники: [audit.md](audit.md) ↔ [HANDBOOK.md](HANDBOOK.md); аудиты 2026-05-28 (`app/`, `static/`, `scripts/`).  
> Принципы: [§0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки) (LAN, один оператор, UX > security theater).  
> Инциденты: [docs/RUNBOOK.md](docs/RUNBOOK.md). План: [§22](HANDBOOK.md#22-планируемые-действия).

**Спринт P0–P7 (2026-05-28/29) закрыт** — детали в [журнале HANDBOOK](HANDBOOK.md#журнал-прогресса) и коммите `1926b4a`. Ниже только **открытые** и **отложенные** пункты.

---

## Результат сверки audit ↔ HANDBOOK

| Вердикт | Пункты |
|---------|--------|
| **Уже в проекте** | `HeavyJobQueue` (§12.4), `SqlAlchemyUnitOfWork`, vision `llm_data`, resume/F5, WS sweeper, SSRF/trusted internal, design tokens (`tokens.css`, split CSS), `tests/safety.py` |
| **Отложить без замера** | `BE-SD-HTTPX` — после профилирования очереди jobs (§12.4) |
| **Не планировать** | Prompt-injection theater, жёсткий `ALLOWED_LLM_URLS` по умолчанию, Prometheus, Redis-кэш контекста, Service Worker |
| **По измеримой боли** | См. [низкий приоритет](#низкий-приоритет-только-при-измеримой-боли) |

---

## Сильные стороны (сохранять)

- Слои `api/` → `services/` → `integrations/` → `db/`; `AppError` и коды WS-ошибок.
- Vision: `llm_data`, URL-first в контексте ([§0.1](HANDBOOK.md#01-что-строим)).
- Фронт: fingerprint + append-only `loadMessages`; `LightboxImage.detach()`; user-текст через `textContent` в macros.
- CSS: [tokens.css](static/css/tokens.css) → `chat-layout.css` / `chat-messages.css` / `gallery.css` / `login.css`; `prefers-reduced-motion`; `focus-visible`.
- Тесты: `tests/safety.py`; `pytest -m "not load"`.
- Ops: backup/restore с `set -euo pipefail`, rotation, manifest, pre-restore safety backup.

**Smoke после UI/CSS:** chat (desktop/mobile), gallery, login, macros, `/health`, light/dark, `prefers-reduced-motion: reduce`.

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
| Унифицировать импорты `async_session_factory` | `session.py` vs `db_session` в scripts/legacy ([§14.4](HANDBOOK.md#144-парадигма-pytest-обязательно-для-новых-тестов)) |
| Расширить `SqlAlchemyUnitOfWork` или убрать из hot path | `db/uow.py` — сейчас в основном `media_registry.py` |
| Instance-scoped cache в `LLMClient` | `llm_client.py` global `_MODEL_CACHE` |

---

## Не включать (audit отклонён по HANDBOOK)

| Пункт audit | Причина |
|-------------|---------|
| UnitOfWork как в audit §1.2 | Уже есть `SqlAlchemyUnitOfWork`; **расширить использование**, не дублировать |
| Санитизация prompt injection (§8 audit) | [§0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки), [SECURITY.md](SECURITY.md) |
| `ALLOWED_LLM_URLS` whitelist (§4 audit) | Конфликтует с runtime override из UI; SSRF/trusted internal уже есть |
| Prometheus /metrics (§6 audit) | Избыточно для одного оператора; JSON-логи + `log_turn_context` |
| WS heartbeat + внешний cancel API (§7 audit) | F5/resume, sweeper, cancel через WS `cancel` |
| Service Worker оффлайн (§6 FE audit) | Вне scope v1 ([§0.3](HANDBOOK.md#03-не-цели-версии-1-v1)) |
| Property-based / нагрузочные тесты как обязательные | По запросу; интеграционных тестов достаточно для LAN |

---

## P1 — эксплуатация (из §22 handbook)

| Задача | Когда |
|--------|-------|
| Basic Auth / HTTPS ([nginx template](deploy/nginx-web-chat.conf.template)) | Хост доступен не только вам |
| Чеклист §7 на стенде | Пинг LLM/SD, `PUBLIC_BASE_URL`, `systemctl is-enabled` |

`data/uploads/` — в `.gitignore`; ops: бэкап uploads при `WEB_CHAT_BACKUP_UPLOADS=1` ([DATABASE-BACKUP.md](deploy/DATABASE-BACKUP.md)).

---

*При закрытии задачи — журнал в [HANDBOOK.md](HANDBOOK.md#журнал-прогресса); при архитектурных сдвигах — §12.4 / §22.*
