# web-chat

**Монолитный веб-чат для локальной сети** с AI-агентом, встроенным MCP и генерацией изображений через Stable Diffusion (Automatic1111 / Forge).

Один процесс (Uvicorn) объединяет UI в браузере, REST/WebSocket API, оркестрацию LLM, tool calling и раздачу медиа. Внешние зависимости — только **LLM** (OpenAI-compatible) и **SD WebUI** в той же LAN.

| Документ | Содержание |
|----------|------------|
| **[deploy/DEPLOY.md](deploy/DEPLOY.md)** | Полное развёртывание на Linux, systemd, backup, WireGuard |
| **[TODO.md](TODO.md)** | Архитектура, этапы 1–11, API, риски, дорожная карта v2 |
| **[deploy/wireguard/proxmox-lxc.md](deploy/wireguard/proxmox-lxc.md)** | VPN для LXC на Proxmox |

---

## Возможности

- **Чат** — несколько бесед, стриминг ответа, пресеты системных промптов.
- **Вложения** — изображения (vision), PDF/DOCX/TXT (извлечение текста).
- **Генерация изображений** — `generate_image`, `img2img`, `upscale_images` через SD; картинки в сетке под сообщением (не markdown в тексте).
- **Галерея** — `/gallery`, объединение БД и файлов на диске.
- **Поиск** по истории сообщений, **экспорт** беседы в Markdown.
- **Настройки в UI** — модель LLM, URL LLM/SD, тема, размер шрифта, переименование беседы.
- **Журнал** — кольцевой буфер логов в интерфейсе.
- **Retention** — автоочистка uploads/generated по сроку (timer + фоновая задача).

---

## Архитектура (кратко)

```text
Браузер (LAN / WireGuard)
    │  HTTP :8090  — UI, REST, статика
    │  WS /ws/{conversation_id}
    ▼
web-chat (FastAPI)
    ├── AgentOrchestrator → LLM (.41:8989)
    ├── ToolExecutor → SD tools / extract_text
    ├── MCP :8091 (streamable-http, для внешних клиентов)
    └── SQLite + data/uploads + data/generated
```

**Важно:** в контекст LLM не попадает base64; агент и MCP отдают **HTTP URL** (`PUBLIC_BASE_URL` + `/media/asset/{uuid}`).

---

## Требования

- **Python 3.11+**
- Доступ из хоста web-chat к LLM и SD WebUI по сети
- Для OCR сканов (опционально): `tesseract-ocr` + `pytesseract`

---

## Быстрый старт

```bash
cd /root/web-chat   # или /opt/web-chat

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# Отредактируйте PUBLIC_BASE_URL, LLM_BASE_URL, SD_WEBUI_URL

./restart.sh dev
# либо: uvicorn app.main:app --host 0.0.0.0 --port 8090
```

Откройте в браузере: **`http://<IP-хоста>:8090/`** (тот же хост, что в `PUBLIC_BASE_URL`).

Проверка API:

```bash
curl -s http://127.0.0.1:8090/api/health
# {"status":"ok","llm":"ok","sd":"ok","public_base_url":"...","timeouts_ok":true}
```

---

## Запуск и управление

### Скрипт `restart.sh`

| Команда | Действие |
|---------|----------|
| `./restart.sh` | Перезапуск через **systemd** (`web-chat.service`), иначе uvicorn в dev |
| `./restart.sh dev` | Всегда uvicorn в фоне → `logs/uvicorn.log` |
| `./restart.sh status` | Health, порты, статус unit |
| `./restart.sh --help` | Справка |

Переменные читаются из `.env` (`WEB_PORT`, `PUBLIC_BASE_URL`), если не заданы в окружении.

Примеры:

```bash
./restart.sh status
SKIP_SYSTEMD=1 ./restart.sh dev
WEB_CHAT_BASE_URL=http://192.168.88.44:8090 ./restart.sh
```

### systemd (production)

```bash
sudo cp deploy/web-chat.service deploy/web-chat-cleanup.* /etc/systemd/system/
# поправьте пути в unit-файлах
sudo systemctl enable --now web-chat.service web-chat-cleanup.timer
./restart.sh
```

Подробности: **[deploy/DEPLOY.md](deploy/DEPLOY.md)**.

---

## Конфигурация (`.env`)

Полный пример: **[.env.example](.env.example)**.

| Переменная | Назначение |
|------------|------------|
| `PUBLIC_BASE_URL` | URL в браузере (картинки, MCP) — **критично** |
| `WEB_PORT` | HTTP-порт (8090) |
| `MCP_PORT` | MCP (0 = WEB_PORT+1) |
| `LLM_BASE_URL` | OpenAI-compatible API |
| `SD_WEBUI_URL` | Stable Diffusion WebUI |
| `REQUEST_TIMEOUT` / `MCP_TIMEOUT` | Таймауты SD; MCP > REQUEST |
| `DATABASE_URL` | SQLite (по умолчанию `data/db/...`) |
| `MAX_*` | Лимиты upload, истории, tool rounds |
| `UPLOAD_RETENTION_DAYS` / `GENERATED_RETENTION_DAYS` | Срок хранения файлов |

Настройки LLM/SD можно переопределить **в UI** (сохраняются в `localStorage` и передаются в WebSocket) — см. панель «Настройки».

---

## Внешние сервисы (по умолчанию)

| Сервис | URL |
|--------|-----|
| LLM | http://192.168.88.41:8989/v1 |
| SD WebUI | http://192.168.88.52:7860 |
| image-gen (референс) | http://192.168.88.16:8081/mcp |

---

## API (основное)

Префикс REST: `/api`.

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Статус процесса, LLM, SD, `public_base_url`, `timeouts_ok` |
| GET/POST | `/conversations` | Список / создание бесед |
| GET/PATCH/DELETE | `/conversations/{id}` | Одна беседа |
| GET | `/conversations/{id}/export` | Скачать Markdown |
| GET | `/conversations/{id}/messages` | История (`limit`, `before`) |
| GET | `/search?q=...` | Поиск по тексту сообщений |
| GET | `/presets` | Пресеты |
| POST | `/upload` | Загрузка файлов |
| GET | `/gallery` | JSON галереи |
| WS | `/ws/{conversation_id}` | Чат, стриминг, `user_message`, `cancel`, `regenerate` |

Медиа: `/media/asset/{uuid}`, `/media/uploads/...`, `/media/generated/...`.

Страницы: `/`, `/gallery`.

---

## Структура репозитория

```text
app/
  api/           REST, WebSocket, страницы, галерея
  db/            модели, репозитории, seed, миграции SQLite
  integrations/  LLM, SD, MCP, документы, tools
  services/      агент, медиа, cleanup, экспорт
  scripts/       run_cleanup, test_agent
static/          CSS, JS (chat, gallery, markdown)
templates/       Jinja2 (chat.html, gallery.html)
tests/           pytest (72 теста)
deploy/          systemd, backup, logrotate, WireGuard
data/            runtime (не в git): db, uploads, generated
logs/            uvicorn.log (dev, не в git)
```

---

## Разработка

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt

ruff check app tests
ruff format app tests
pytest -q
```

Конфигурация инструментов: **[pyproject.toml](pyproject.toml)** (pytest, ruff).

Ручной прогон агента без UI:

```bash
python -m app.scripts.test_agent "Нарисуй закат над морем"
```

---

## Производство и эксплуатация

| Задача | Инструмент |
|--------|------------|
| Развёртывание | [deploy/DEPLOY.md](deploy/DEPLOY.md) |
| Перезапуск | `./restart.sh` |
| Бэкап БД | `deploy/backup-data.sh` |
| Логи | journald или `deploy/logrotate-web-chat.conf` |
| VPN | `deploy/wireguard/` |

Перед выходом в LAN/production проверьте `GET /api/health` и чеклист в **TODO.md §7**.

---

## Статус проекта

- Этапы разработки **1–11** — выполнены.
- Часть **v2**: поиск, экспорт Markdown, inline-заголовок, галерея, img2img/upscale.
- **72** автотеста (`pytest`).
- Дорожная карта (PostgreSQL, auth, RAG, multi-user) — в **TODO.md §17**.

---

## Лицензия и вклад

Внутренний проект для домашней/LAN инфраструктуры. При изменении архитектуры обновляйте **TODO.md** в том же коммите, что и код.
