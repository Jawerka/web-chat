# web-chat

Монолитное Python-приложение для LAN: веб-чат с AI-агентом, встроенным MCP и генерацией изображений через Stable Diffusion.

Подробный план разработки: [TODO.md](TODO.md).

## Требования

- Python 3.11+
- Доступ в LAN к LLM (`192.168.88.41:8989`) и SD WebUI (`192.168.88.52:7860`) — на этапах интеграции

## Быстрый старт

```bash
cd /root/web-chat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Отредактируйте PUBLIC_BASE_URL — URL, который вводите в браузере
uvicorn app.main:app --host 0.0.0.0 --port 8090   # доступ из LAN
```

Проверка:

```bash
curl -s http://localhost:8090/api/health
# {"status":"ok"}
```

UI (после этапа 7): `http://<хост-LAN>:8090/`

## Внешние сервисы

| Сервис | URL по умолчанию |
|--------|------------------|
| LLM (OpenAI-compatible) | http://192.168.88.41:8989/v1 |
| SD WebUI | http://192.168.88.52:7860 |
| image-gen (референс MCP) | http://192.168.88.16:8081/mcp |

## Структура

```
app/          — код приложения
data/         — SQLite, uploads, generated (не в git)
static/       — CSS/JS
templates/    — Jinja2
tests/        — pytest
deploy/       — systemd unit
```

## Разработка

```bash
source .venv/bin/activate
ruff check app tests
ruff format app tests
pytest
```

## systemd

Шаблон unit-файла: [deploy/web-chat.service](deploy/web-chat.service).
