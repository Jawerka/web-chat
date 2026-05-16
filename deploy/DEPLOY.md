# Развёртывание web-chat на Linux

Пошаговое руководство для bare metal, VM или LXC (в т.ч. Proxmox).  
Приложение рассчитано на **локальную сеть (LAN)**; удалённый доступ — через **WireGuard** (см. [wireguard/proxmox-lxc.md](wireguard/proxmox-lxc.md)).

---

## Содержание

1. [Требования](#1-требования)
2. [Подготовка системы](#2-подготовка-системы)
3. [Установка (рекомендуется)](#3-установка-рекомендуется)
4. [Ручная установка](#4-ручная-установка)
5. [Конфигурация .env](#5-конфигурация-env)
6. [Первый запуск и проверка](#6-первый-запуск-и-проверка)
7. [systemd и автозапуск](#7-systemd-и-автозапуск)
8. [Резервное копирование и логи](#8-резервное-копирование-и-логи)
9. [WireGuard (опционально)](#9-wireguard-опционально)
10. [Обновление и откат](#10-обновление-и-откат)
11. [Устранение неполадок](#11-устранение-неполадок)

---

## 1. Требования

### Аппаратные (ориентир)

| Ресурс | Минимум | Рекомендуется |
|--------|---------|---------------|
| CPU | 2 ядра | 4+ (при активной SD-генерации нагрузка на SD-хост, не на web-chat) |
| RAM | **2 GB** | **4+ GB** (SQLite + кэш вложений; vision сжимает картинки до ~6 MB) |
| Диск | **10 GB** свободно | **50+ GB** (`data/generated/`, uploads, SQLite) |

web-chat сам по себе лёгкий; узкое место — **внешние** LLM и SD WebUI.

### Программные (на хосте web-chat)

| Компонент | Версия / наличие |
|-----------|------------------|
| ОС | Linux: **Ubuntu 22.04+**, **Debian 12+**, Proxmox LXC |
| Python | **3.11+** (3.12 предпочтительно) |
| Пакеты | `python3-venv`, `curl`, `sqlite3`, `build-essential` (для wheels) |
| systemd | для production и **автозапуска после перезагрузки** |
| Сеть | Исходящий HTTP к LLM и SD из процесса web-chat |

Опционально: `tesseract-ocr` + `pytesseract` — OCR в PDF/сканах.

### Внешние сервисы (не входят в репозиторий)

| Сервис | Назначение | URL по умолчанию |
|--------|------------|------------------|
| LLM | Чат, tools, vision | `http://192.168.88.41:8989/v1` |
| SD WebUI | txt2img, img2img, upscale | `http://192.168.88.52:7860` |

SD WebUI должен быть запущен с **`--api`**.

### Порты на хосте web-chat

| Порт | Сервис |
|------|--------|
| `WEB_PORT` (8090) | HTTP: UI, REST, WebSocket, `/media/*` |
| `MCP_PORT` (8091 или `WEB_PORT+1`) | Встроенный MCP (streamable-http) |

---

## 2. Подготовка системы

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  curl git sqlite3 \
  build-essential libffi-dev

# Опционально: OCR
# sudo apt install -y tesseract-ocr tesseract-ocr-rus
```

### Каталог проекта

```bash
sudo mkdir -p /opt/web-chat
sudo chown "$USER:$USER" /opt/web-chat
cd /opt/web-chat
git clone <URL-репозитория> .   # или rsync с dev-машины
```

Каталог `data/` **не в git** — создаётся при установке.

---

## 3. Установка (рекомендуется)

Скрипт **`deploy/install.sh`** выполняет:

1. Проверку Python 3.11+, структуры проекта  
2. Создание `.venv` и `pip install -r requirements.txt`  
3. Копирование `.env.example` → `.env` (если нет `.env`)  
4. Каталоги `data/db`, `data/uploads`, `data/generated`, `logs/`  
5. Генерацию systemd unit из **шаблонов** (`*.service.template`)  
6. `systemctl enable --now web-chat.service` и timer очистки  
7. Опционально `pytest` и проверку `/api/health`

```bash
cd /opt/web-chat
chmod +x deploy/install.sh restart.sh deploy/backup-data.sh

# Production с автозапуском (нужен root для systemd)
sudo ./deploy/install.sh

# Другой каталог / пользователь
sudo ./deploy/install.sh \
  --install-root /opt/web-chat \
  --user www-data \
  --group www-data

# Только venv + .env, без systemd (разработка)
./deploy/install.sh --skip-systemd --skip-tests

# С dev-зависимостями (pytest, ruff)
./deploy/install.sh --skip-systemd --dev-deps

# Logrotate для logs/*.log (режим restart.sh dev)
sudo ./deploy/install.sh --logrotate
```

После установки отредактируйте **`.env`** (минимум `PUBLIC_BASE_URL`, `LLM_BASE_URL`, `SD_WEBUI_URL`), затем:

```bash
sudo systemctl restart web-chat
./restart.sh status
```

### Удаление systemd unit

```bash
sudo ./deploy/install.sh --uninstall
```

### Шаблоны и сгенерированные файлы

| Файл | Назначение |
|------|------------|
| `deploy/web-chat.service.template` | Основной сервис Uvicorn |
| `deploy/web-chat-cleanup.service.template` | Oneshot очистки retention |
| `deploy/web-chat-cleanup.timer` | Ежедневный запуск cleanup |
| `deploy/logrotate-web-chat.conf.template` | Ротация `logs/*.log` |
| `deploy/generated/` | Сгенерированные unit (не коммитить) |

Плейсхолдеры: `@@INSTALL_ROOT@@`, `@@RUN_USER@@`, `@@RUN_GROUP@@`, `@@WEB_PORT@@`.

Пример готового unit для `/root/web-chat` (без скрипта): `deploy/web-chat.service`.

---

## 4. Ручная установка

Если systemd не нужен:

```bash
cd /opt/web-chat
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt   # или requirements-dev.txt

cp .env.example .env
nano .env

mkdir -p data/db data/uploads data/generated/thumbs logs
./restart.sh dev
```

---

## 5. Конфигурация .env

### Обязательно проверить

| Переменная | Описание |
|------------|----------|
| `PUBLIC_BASE_URL` | URL в браузере, **без** `/` в конце. Пример: `http://192.168.88.44:8090` |
| `LLM_BASE_URL` | OpenAI-compatible, обычно с `/v1` |
| `SD_WEBUI_URL` | Базовый URL WebUI, **без** `/sdapi` |
| `MCP_TIMEOUT` | Должен быть **>** `REQUEST_TIMEOUT` (`timeouts_ok` в health) |

### Vision (большие фото)

| Переменная | По умолчанию |
|------------|--------------|
| `LLM_VISION_MAX_BYTES` | 6291456 (~6 MB) |
| `LLM_VISION_JPEG_QUALITY` | 88 |
| `LLM_VISION_MAX_SIDE_PX` | 4096 |

Ассеты отдаются LLM по `GET /media/asset/{id}/llm` (сжатый JPEG).

### Фаервол (пример UFW)

```bash
sudo ufw allow 8090/tcp comment 'web-chat'
# UDP 51820 — если WireGuard на этом же хосте
```

---

## 6. Первый запуск и проверка

### Скрипт `restart.sh`

| Команда | Действие |
|---------|----------|
| `./restart.sh` | Перезапуск **systemd**, иначе dev |
| `./restart.sh dev` | Uvicorn в фоне → `logs/uvicorn.log` |
| `./restart.sh status` | Health + systemd + порты |

### Проверки

```bash
curl -s http://127.0.0.1:8090/api/health | jq .
# status: ok | degraded
# llm, sd, public_base_url, timeouts_ok

curl -s http://127.0.0.1:8090/api/presets | jq '.[].slug'
```

В браузере с другой машины в LAN:

- Чат: `http://<IP>:8090/`
- Макросы: `http://<IP>:8090/macros`
- Галерея: `http://<IP>:8090/gallery`

### Автотесты

```bash
source .venv/bin/activate
pytest -q
# ожидается: 88 passed
```

### После перезагрузки ОС

При установке через `install.sh` сервис **включён в автозагрузку**:

```bash
sudo systemctl is-enabled web-chat.service   # enabled
sudo systemctl status web-chat
```

При активной генерации SD/LLM UI после F5 **восстанавливает** черновик из БД (см. TODO.md §20).

---

## 7. systemd и автозапуск

### Управление

```bash
sudo systemctl status web-chat
sudo journalctl -u web-chat -f
./restart.sh
```

### Смена пользователя сервиса

```bash
sudo ./deploy/install.sh --user www-data --group www-data
sudo chown -R www-data:www-data /opt/web-chat/data /opt/web-chat/logs
```

### Timer очистки

```bash
sudo systemctl list-timers web-chat-cleanup.timer
sudo systemctl start web-chat-cleanup.service   # разовый прогон
```

Retention: `UPLOAD_RETENTION_DAYS`, `GENERATED_RETENTION_DAYS` в `.env`.

---

## 8. Резервное копирование и логи

### Бэкап SQLite

```bash
WEB_CHAT_ROOT=/opt/web-chat ./deploy/backup-data.sh

# С каталогом генераций:
WEB_CHAT_BACKUP_GENERATED=1 WEB_CHAT_ROOT=/opt/web-chat ./deploy/backup-data.sh
```

Cron:

```cron
0 3 * * * root WEB_CHAT_ROOT=/opt/web-chat /opt/web-chat/deploy/backup-data.sh
```

### Логи

| Источник | Где |
|----------|-----|
| systemd | `journalctl -u web-chat` |
| dev | `logs/uvicorn.log` |
| UI «Журнал» | `GET/DELETE /api/logs` |

```bash
sudo ./deploy/install.sh --logrotate
# или: sudo cp deploy/logrotate-web-chat.conf /etc/logrotate.d/web-chat
```

---

## 9. WireGuard (опционально)

См. **[deploy/wireguard/proxmox-lxc.md](wireguard/proxmox-lxc.md)**.

После VPN обновите:

```env
PUBLIC_BASE_URL=http://10.88.0.44:8090
```

```bash
./restart.sh
```

---

## 10. Обновление и откат

```bash
cd /opt/web-chat
./deploy/backup-data.sh

git pull
source .venv/bin/activate
pip install -r requirements.txt
pytest -q

# Перегенерировать unit при смене пути/пользователя:
sudo ./deploy/install.sh --skip-tests

./restart.sh
```

Откат: восстановить `data/db/*.sqlite` из архива, `git checkout <tag>`.

**Важно:** не копируйте SQLite при работающем процессе (WAL).

---

## 11. Устранение неполадок

| Симптом | Решение |
|---------|---------|
| После reboot сервис не поднялся | `systemctl status web-chat`, `journalctl -u web-chat -n 50` |
| `health`: `degraded`, `sd: unavailable` | SD URL, `--api`, фаервол с хоста web-chat |
| `health`: `llm: unavailable` | `curl "$LLM_BASE_URL/models"` с хоста web-chat |
| Картинки битые | `PUBLIC_BASE_URL` ≠ URL в браузере |
| После F5 пустой пузырь при генерации | Обновить код (resume); WS `connected` с `in_progress` |
| `database is locked` | Не копировать БД при работающем uvicorn |
| Порт занят | `ss -tlnp \| grep 8090` или `./restart.sh dev` |
| pytest падает | `pip install -r requirements-dev.txt` |

### Чеклист перед production

- [ ] С хоста web-chat доступны LLM и SD (`curl` / health)
- [ ] `PUBLIC_BASE_URL` = URL в браузере
- [ ] SD с `--api`
- [ ] `.env` не в git; `chmod 750 data/`
- [ ] `sudo ./deploy/install.sh` → `enabled` в systemd
- [ ] Backup в cron
- [ ] Ручной QA: TODO.md §14.3 и §20

---

*См. также: [README.md](../README.md), [TODO.md](../TODO.md).*
