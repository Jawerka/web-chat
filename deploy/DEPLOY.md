# Развёртывание web-chat на Linux

Пошаговое руководство для bare metal, VM или LXC (в т.ч. Proxmox).  
Приложение рассчитано на **локальную сеть (LAN)**; удалённый доступ — через **WireGuard** (см. [wireguard/proxmox-lxc.md](wireguard/proxmox-lxc.md)).

---

## Содержание

1. [Требования](#1-требования)
2. [Подготовка системы](#2-подготовка-системы)
3. [Установка приложения](#3-установка-приложения)
4. [Конфигурация .env](#4-конфигурация-env)
5. [Первый запуск и проверка](#5-первый-запуск-и-проверка)
6. [systemd (production)](#6-systemd-production)
7. [Резервное копирование и логи](#7-резервное-копирование-и-логи)
8. [WireGuard (опционально)](#8-wireguard-опционально)
9. [Обновление и откат](#9-обновление-и-откат)
10. [Устранение неполадок](#10-устранение-неполадок)

---

## 1. Требования

### Аппаратные (ориентир)

| Ресурс | Минимум | Рекомендуется |
|--------|---------|---------------|
| CPU | 2 ядра | 4+ |
| RAM | 2 GB | 4+ GB |
| Диск | 10 GB | 50+ GB (с генерациями) |

### Программные

| Компонент | Версия |
|-----------|--------|
| ОС | Linux (Ubuntu 22.04+, Debian 12+) |
| Python | **3.11+** |
| Сеть | Доступ к LLM и SD WebUI из контейнера/хоста |

### Внешние сервисы (не входят в репозиторий)

| Сервис | Назначение | URL по умолчанию |
|--------|------------|------------------|
| LLM | Чат, tools, vision | `http://192.168.88.41:8989/v1` |
| SD WebUI | txt2img, img2img, upscale | `http://192.168.88.52:7860` |

SD WebUI должен быть запущен с **`--api`**.

---

## 2. Подготовка системы

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  curl git sqlite3 \
  build-essential libffi-dev

# Опционально: OCR в PDF/сканах
# sudo apt install -y tesseract-ocr tesseract-ocr-rus
# pip install pytesseract  # при необходимости
```

### Пользователь и каталог

```bash
sudo mkdir -p /opt/web-chat
sudo chown "$USER:$USER" /opt/web-chat
cd /opt/web-chat
git clone <URL-репозитория> .   # или scp/rsync из /root/web-chat
```

### Каталоги данных

При первом запуске создаются автоматически; можно заранее:

```bash
mkdir -p data/db data/uploads data/generated data/generated/thumbs logs
chmod 750 data
```

`data/` **не коммитится** в git (см. `.gitignore`).

---

## 3. Установка приложения

```bash
cd /opt/web-chat   # или /root/web-chat

python3 -m venv .venv
source .venv/bin/activate

# Production
pip install --upgrade pip
pip install -r requirements.txt

# Разработка (+ pytest, ruff)
pip install -r requirements-dev.txt

cp .env.example .env
nano .env
```

Альтернатива через `pyproject.toml`:

```bash
pip install -e ".[dev]"
```

---

## 4. Конфигурация .env

### Обязательно проверить

| Переменная | Описание |
|------------|----------|
| `PUBLIC_BASE_URL` | Точный URL в браузере, **без** завершающего `/`. Пример: `http://192.168.88.44:8090` |
| `LLM_BASE_URL` | OpenAI-compatible endpoint, обычно с `/v1` |
| `SD_WEBUI_URL` | Базовый URL WebUI, без `/sdapi` |
| `MCP_TIMEOUT` | Должен быть **>** `REQUEST_TIMEOUT` (проверка в `/api/health` → `timeouts_ok`) |

### Порты

| Порт | Сервис |
|------|--------|
| `WEB_PORT` (8090) | HTTP UI + REST + WebSocket |
| `MCP_PORT` (8091 или WEB+1) | Встроенный MCP (streamable-http) |

Фаервол (пример UFW):

```bash
sudo ufw allow 8090/tcp comment 'web-chat'
# UDP 51820 — только если WireGuard в этом же CT
```

---

## 5. Первый запуск и проверка

### Ручной запуск (разработка)

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8090
```

### Скрипт перезапуска

```bash
chmod +x restart.sh
./restart.sh dev          # uvicorn + logs/uvicorn.log
./restart.sh status       # health + systemd
./restart.sh              # systemd, иначе dev
```

### Проверки

```bash
curl -s http://127.0.0.1:8090/api/health | jq .
# status: ok | degraded
# llm, sd, public_base_url, timeouts_ok

curl -s http://127.0.0.1:8090/api/presets | jq '.[].slug'
# default, image_gen, document_analysis
```

В браузере с другой машины в LAN:

- Чат: `http://<IP-хоста>:8090/`
- Галерея: `http://<IP-хоста>:8090/gallery`

### Автотесты

```bash
source .venv/bin/activate
pytest -q
ruff check app tests
```

Ожидается: **72 passed**.

---

## 6. systemd (production)

### Установка unit-файлов

```bash
sudo cp deploy/web-chat.service /etc/systemd/system/
sudo cp deploy/web-chat-cleanup.service /etc/systemd/system/
sudo cp deploy/web-chat-cleanup.timer /etc/systemd/system/

# Пути в unit должны совпадать с реальным каталогом:
# WorkingDirectory=/opt/web-chat
# EnvironmentFile=/opt/web-chat/.env
# ExecStart=/opt/web-chat/.venv/bin/uvicorn ...

sudo systemctl daemon-reload
sudo systemctl enable --now web-chat.service
sudo systemctl enable --now web-chat-cleanup.timer
```

### Управление

```bash
sudo systemctl status web-chat
sudo journalctl -u web-chat -f
./restart.sh
```

### Пример правки путей в `web-chat.service`

```ini
[Service]
WorkingDirectory=/opt/web-chat
EnvironmentFile=/opt/web-chat/.env
ExecStart=/opt/web-chat/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090
User=www-data
Group=www-data
Restart=on-failure
RestartSec=5
```

При смене пользователя: `chown -R www-data:www-data /opt/web-chat/data`.

---

## 7. Резервное копирование и логи

### Бэкап SQLite

```bash
chmod +x deploy/backup-data.sh
sudo WEB_CHAT_ROOT=/opt/web-chat WEB_CHAT_BACKUP_DIR=/var/backups/web-chat \
  ./deploy/backup-data.sh

# С каталогом генераций:
WEB_CHAT_BACKUP_GENERATED=1 ./deploy/backup-data.sh
```

Добавьте в cron:

```cron
0 3 * * * root WEB_CHAT_ROOT=/opt/web-chat /opt/web-chat/deploy/backup-data.sh
```

### Логи приложения

| Источник | Где |
|----------|-----|
| systemd | `journalctl -u web-chat` |
| dev (`restart.sh dev`) | `logs/uvicorn.log` |
| UI «Журнал» | Кольцевой буфер в памяти, `GET/DELETE /api/logs` |

Ротация файлов (если пишете в `logs/`):

```bash
sudo cp deploy/logrotate-web-chat.conf /etc/logrotate.d/web-chat
```

---

## 8. WireGuard (опционально)

Для доступа извне LAN без проброса портов на роутере:

1. WireGuard на **Proxmox-хосте** (сервер VPN).
2. В LXC web-chat — интерфейс `wg0` (подсеть `10.88.0.0/24`).

Подробно: **[deploy/wireguard/proxmox-lxc.md](wireguard/proxmox-lxc.md)**.

После поднятия туннеля:

```env
PUBLIC_BASE_URL=http://10.88.0.44:8090
```

Перезапуск: `./restart.sh`.

---

## 9. Обновление и откат

```bash
cd /opt/web-chat
./deploy/backup-data.sh

git pull
source .venv/bin/activate
pip install -r requirements.txt
pytest -q

./restart.sh
```

Откат: восстановить БД из архива `web-chat-*.tar.gz`, `git checkout <tag>`.

---

## 10. Устранение неполадок

| Симптом | Решение |
|---------|---------|
| `health`: `degraded`, `sd: unavailable` | Проверить SD URL, `--api`, фаервол |
| `health`: `llm: unavailable` | `curl LLM_BASE_URL/models` с хоста web-chat |
| Картинки битые в чате | `PUBLIC_BASE_URL` ≠ URL в браузере |
| `database is locked` | Уже mitigated (WAL, commit до tools); не копировать БД при работающем процессе |
| `MCP_TIMEOUT` warning | Увеличить `MCP_TIMEOUT` > `REQUEST_TIMEOUT` |
| Порт занят | `fuser -k 8090/tcp` или `./restart.sh dev` |
| pytest падает | Временная БД в `tmp_path`; `pip install -r requirements-dev.txt` |

### Чеклист перед production

- [ ] С хоста web-chat доступны LLM и SD
- [ ] `PUBLIC_BASE_URL` совпадает с браузером
- [ ] SD с `--api`
- [ ] `.env` не в git; права на `data/` ограничены
- [ ] Настроен backup + systemd + (опционально) logrotate
- [ ] Прогнан ручной QA из [TODO.md](../TODO.md) §14.3

---

*См. также: [README.md](../README.md), [TODO.md](../TODO.md).*
