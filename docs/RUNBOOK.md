# Runbook — web-chat (домашний стенд)

> Контекст: [HANDBOOK.md §0.5](../HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки) — один оператор, LAN.  
> Дашборд: `http://<хост>:8090/health` · API: `GET /api/health`

---

## LLM недоступен

**Симптомы:** в чате ошибка `llm_error`, `/health` — LLM красный, нет ответов на текст.

1. С хоста web-chat: `curl -sS "$LLM_BASE_URL/models"` (или `/v1/models`).
2. Проверить процесс llama.cpp / vLLM на целевом IP.
3. В `.env`: `LLM_BASE_URL`, таймаут `LLM_TIMEOUT_SEC`.
4. В настройках чата (override URL) — не подставлен ли неверный адрес; при auth — `POST /api/config/trusted-internal/sync`.
5. `journalctl -u web-chat -n 80` или `logs/web-chat.log`.

---

## SD WebUI недоступен

**Симптомы:** tools `generate_image` / `img2img` падают, health — SD unavailable.

1. `curl -sS "$SD_WEBUI_URL/sdapi/v1/sd-models"` с хоста web-chat.
2. WebUI запущен с **`--api`**.
3. `REQUEST_TIMEOUT` / `MCP_TIMEOUT` (MCP > REQUEST).
4. GPU/VRAM: логи WebUI на машине .52.
5. Override `sd_webui_url` в UI — сверить с реальным хостом.

---

## Диск заполнен

**Симптомы:** upload 507/500, health disk warning, SQLite errors.

1. `df -h` на разделе с `data/`.
2. Очистка: `data/generated/`, старые `data/uploads/` (retention: `UPLOAD_RETENTION_DAYS`, `GENERATED_RETENTION_DAYS`).
3. Галерея: «Очистить сироты», при необходимости purge в UI.
4. Бэкапы: `data/backups/` — вынести архивы на другой диск ([DATABASE-BACKUP.md](../deploy/DATABASE-BACKUP.md)).
5. PostgreSQL: `VACUUM` / размер БД, если `MediaAsset` разросся (BLOB).

---

## Очередь / генерация «зависла»

**Симптомы:** статус «Генерация…» не снимается, Stop не помогает, после F5 — resume.

1. `/health` — активные WS, job queue (если отображается).
2. `POST` cancel через UI (Stop) или перезагрузка страницы → WS `connected` + `generation-status`.
3. `journalctl -u web-chat -f` — tool loop, SD timeout.
4. Крайний случай: `systemctl restart web-chat` (черновик assistant в БД — resume после F5).
5. Проверить `MAX_TOOL_ROUNDS` — не исчерпан ли лимит с частичным ответом.

---

## Быстрые команды

```bash
sudo systemctl status web-chat
sudo journalctl -u web-chat -n 100 --no-pager
curl -s http://127.0.0.1:8090/api/health | jq
cd /root/web-chat && source .venv/bin/activate && pytest -q
```

---

## Восстановление из бэкапа

Полная документация: [deploy/DATABASE-BACKUP.md](../deploy/DATABASE-BACKUP.md).

### Квартальная проверка restore (чеклист)

Раз в ~3 месяца на **копии** стенда (VM/другой хост), не на боевом без остановки:

1. Свежий бэкап: `./scripts/backup-database.sh` — убедиться, что архив появился в `data/backups/database/`.
2. `systemctl stop web-chat` на тестовой копии.
3. `./scripts/restore-database.sh --list` — выбрать архив (`--index 1` или `--stamp …`).
4. `./scripts/restore-database.sh --yes` (создаётся safety-backup текущей БД).
5. `systemctl start web-chat` → `curl -s http://127.0.0.1:8090/api/health | jq`.
6. В браузере: вход, одна беседа, одно сообщение с картинкой (если были в бэкапе).
7. Зафиксировать дату проверки в заметках оператора.

При Postgres сверка схемы: `python -m app.scripts.verify_migration --target "$DATABASE_URL"` (см. DATABASE-BACKUP.md).

---

## nginx + HTTPS (если хост не только в LAN)

> Не обязательно для одного пользователя в домашней сети — см. [HANDBOOK §0.5](../HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки).

1. Шаблон: [deploy/nginx-web-chat.conf.template](../deploy/nginx-web-chat.conf.template) — upstream на `127.0.0.1:8090`, WebSocket upgrade.
2. TLS: Let's Encrypt или свой сертификат; в `.env`: `AUTH_COOKIE_SECURE=true`, `WEB_CHAT_ENV=production`.
3. `TRUSTED_PROXY_IPS=127.0.0.1`, `TRUSTED_WS_ORIGINS=https://<ваш-хост>`.
4. Опционально: Basic Auth в nginx **или** `API_ACCESS_KEY` в приложении — [SECURITY.md](../SECURITY.md).
5. `nginx -t && systemctl reload nginx` → проверка чата и `/health` по HTTPS.
