# web-chat

**Монолитный веб-чат для локальной сети** с AI-агентом, встроенным MCP и генерацией изображений через Stable Diffusion (Automatic1111 / Forge).

Один процесс (Uvicorn) объединяет UI в браузере, REST/WebSocket API, оркестрацию LLM, tool calling и раздачу медиа. Внешние зависимости — только **LLM** (OpenAI-compatible) и **SD WebUI** в той же LAN.

| Документ | Содержание |
|----------|------------|
| **[deploy/DEPLOY.md](deploy/DEPLOY.md)** | Требования, `install.sh`, systemd, backup, WireGuard |
| **[TODO.md](TODO.md)** | Архитектура, этапы 1–11, доработки §20, API, риски |
| **[Sys-prompt.md](Sys-prompt.md)** | Эталонные системные промпты пресетов (txt2img, img2img, …) |
| **[deploy/wireguard/proxmox-lxc.md](deploy/wireguard/proxmox-lxc.md)** | VPN для LXC на Proxmox |

---

## Возможности

- **Чат** — несколько бесед, стриминг, пресеты, авто-заголовок и inline-переименование.
- **Вложения** — изображения (vision через `/media/asset/{id}/llm`), PDF/DOCX/TXT.
- **Генерация изображений** — `generate_image`, `img2img`, `upscale_images`; сетка под сообщением.
- **Возобновление после F5** — черновик ассистента в SQLite, poll + WS `connected` с `in_progress`.
- **Быстрые промпты** — `@alias` в поле ввода, страница `/macros`, спойлер в чате.
- **Галерея** — `/gallery`; поиск и экспорт Markdown.
- **Настройки в UI** — модель, URL LLM/SD (runtime override в WS), тема, шрифт.
- **Lightbox** — скачать картинку, прикрепить в composer (скрепка), навигация.
- **Retention** — timer + фоновая очистка uploads/generated.

---

## Архитектура (кратко)

```text
Браузер (LAN / WireGuard)
    │  HTTP :8090  — UI, REST, статика
    │  WS /ws/{conversation_id}
    ▼
web-chat (FastAPI)
    ├── AgentOrchestrator → LLM
    ├── ToolExecutor → SD tools / extract_text
    ├── MCP :8091 (streamable-http)
    └── SQLite + data/uploads + data/generated
```

**Важно:** в контекст LLM не попадает base64; агент отдаёт **HTTP URL** (`PUBLIC_BASE_URL` + `/media/asset/{uuid}`).

---

## Требования

- **Python 3.11+**, Linux, **2 GB RAM** минимум (4 GB рекомендуется)
- Доступ к LLM и SD WebUI с хоста web-chat
- Для production: **systemd** (автозапуск через `deploy/install.sh`)

---

## Быстрый старт (разработка)

```bash
cd /root/web-chat   # или /opt/web-chat

./deploy/install.sh --skip-systemd --dev-deps
nano .env   # PUBLIC_BASE_URL, LLM_BASE_URL, SD_WEBUI_URL

./restart.sh dev
```

Откройте: **`http://<IP-хоста>:8090/`** (тот же хост, что в `PUBLIC_BASE_URL`).

```bash
curl -s http://127.0.0.1:8090/api/health
```

---

## Production (systemd + автозапуск)

```bash
sudo ./deploy/install.sh
sudo systemctl status web-chat
./restart.sh status
```

Unit-файлы генерируются из шаблонов `deploy/*.service.template`. Подробно: **[deploy/DEPLOY.md](deploy/DEPLOY.md)**.

---

## Запуск и управление

| Команда | Действие |
|---------|----------|
| `./restart.sh` | Перезапуск через **systemd**, иначе dev |
| `./restart.sh dev` | Uvicorn → `logs/uvicorn.log` |
| `./restart.sh status` | Health, порты, systemd |
| `sudo systemctl restart web-chat` | После смены `.env` |

---

## Конфигурация (`.env`)

Полный пример: **[.env.example](.env.example)**.

| Переменная | Назначение |
|------------|------------|
| `PUBLIC_BASE_URL` | URL в браузере — **критично** для картинок |
| `WEB_PORT` / `MCP_PORT` | HTTP и MCP |
| `LLM_BASE_URL` / `SD_WEBUI_URL` | Внешние сервисы |
| `REQUEST_TIMEOUT` / `MCP_TIMEOUT` | MCP > REQUEST |
| `LLM_VISION_*` | Сжатие для vision API |

Переопределение LLM/SD/model из **UI** (localStorage + поля WS `llm_base_url`, `sd_webui_url`, `model`).

---

## API (основное)

Префикс REST: `/api`.

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | LLM, SD, `timeouts_ok` |
| GET/POST | `/conversations` | Список / создание |
| GET | `/conversations/{id}/generation-status` | `in_progress`, черновик, `phase` |
| GET | `/conversations/{id}/export` | Markdown |
| GET | `/search?q=` | Поиск по сообщениям |
| CRUD | `/prompt-macros` | Быстрые промпты `@alias` |
| POST | `/upload` | Вложения |
| WS | `/ws/{id}` | Чат, стриминг, `cancel`, `regenerate` |

Медиа: `/media/asset/{uuid}`, `/media/asset/{uuid}/llm`, uploads, generated.  
Страницы: `/`, `/gallery`, `/macros`.

---

## Структура репозитория

```text
app/
  api/           REST, WS, pages, ws_manager, prompt_macros
  services/      agent, streaming_draft, generation_state, macros
  integrations/  LLM, SD, MCP, runtime_config
static/js/       chat.js, prompt-macros.js, gallery.js
deploy/          install.sh, *.template, DEPLOY.md, backup
tests/           pytest (88 тестов)
```

---

## Разработка

```bash
source .venv/bin/activate
ruff check app tests
pytest -q
```

---

## Статус проекта

- Этапы **1–11** и основная часть **v2** — выполнены.
- **88** автотестов; production: **`deploy/install.sh`** + systemd.
- Дорожная карта: **TODO.md §17** (PostgreSQL, auth, RAG).

---

## Лицензия и вклад

Внутренний проект для домашней/LAN инфраструктуры. При изменении архитектуры обновляйте **TODO.md** в том же коммите, что и код. При изменении системных промптов — сначала **Sys-prompt.md**, затем `app/db/seed.py` (см. TODO.md §6).
