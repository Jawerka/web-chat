# Runbook — web-chat (домашний стенд)

> Контекст: [HANDBOOK.md §0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки) — один оператор, LAN.  
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

## LLM vision: `Failed to load image` / `failed to decode image bytes`

**Симптомы:** в чате `llm_error`, в логе llama-server:

```text
handle_media: downloading image from 'http://…/media/asset/{uuid}/llm'
mtmd_helper_bitmap_init_from_buf: failed to decode image bytes
```

1. С хоста **LLM** (не браузера):  
   `curl -sS -o /tmp/llm.jpg -w "%{http_code} %{size_download}\n" "http://<web-chat>/media/asset/<uuid>/llm"`  
   Ожидается `200` и `file /tmp/llm.jpg` → `JPEG image data` (или PNG).
2. **401** — IP LLM не в trusted internal: `LLM_BASE_URL` host, `TRUSTED_INTERNAL_IPS`, `POST /api/config/trusted-internal/sync`.
3. **200, но не JPEG/PNG** — перезапустить web-chat (эндпоинт `/llm` перекодирует WebP → JPEG, сбрасывает битый `llm_data`).
4. Референс во вложении WebP — после фикса отдаётся как JPEG на `/llm`.

---

## SD WebUI недоступен

**Симптомы:** tools `generate_image` / `img2img` падают, health — SD unavailable.

1. `curl -sS "$SD_WEBUI_URL/sdapi/v1/sd-models"` с хоста web-chat.
2. WebUI запущен с **`--api`**.
3. `REQUEST_TIMEOUT` / `MCP_TIMEOUT` (MCP > REQUEST).
4. GPU/VRAM: логи WebUI на машине .52.
5. Override `sd_webui_url` в UI — сверить с реальным хостом.

---

## Gallery → SD WebUI (bridge)

**Симптомы:** «Отправить в SD WebUI» в lightbox; toast ошибки; img2img в SD пустой.

1. На SD-хосте: [`extensions/sd-webui-web-chat-bridge/`](../extensions/sd-webui-web-chat-bridge/) в `stable-diffusion-webui/extensions/`.
2. `curl -sS http://127.0.0.1:7860/web-chat-bridge/ping` — `ok: true`, верный `web_chat_url`.
3. Кнопка в web-chat требует SD-метаданных (`has_metadata` / PNG parameters).
4. `journalctl -u web-chat` — событие `sd_bridge_queued` после клика.
5. SD: вкладка **img2img**, F12 → `[web-chat-bridge]`, аккордеон Web-Chat Bridge.
6. HTTP 502 — SD не принял `POST /web-chat-bridge/push`.

Основной путь — **push-очередь**, не `?web_chat_import=TOKEN`.

---

## Booru → web-chat (Chrome extension)

**Симптомы:** badge `!`; service worker `[booru-web-chat]`.

1. Extension **1.0.2+**, Reload в `chrome://extensions`.
2. Login в web-chat в том же браузере.
3. Options → base URL + host permission.
4. Документация: [`extensions/booru-web-chat/API.md`](../extensions/booru-web-chat/API.md).

---

## Диск заполнен

**Симптомы:** upload 507/500, health disk warning, SQLite errors.

1. `df -h` на разделе с `data/`.
2. Очистка: `data/generated/`, старые `data/uploads/` (retention: `UPLOAD_RETENTION_DAYS`, `GENERATED_RETENTION_DAYS`).
3. Галерея: «Очистить сироты», при необходимости purge в UI.
4. Бэкапы: `data/backups/` — вынести архивы на другой диск ([DATABASE-BACKUP.md](../deploy/DATABASE-BACKUP.md)).
5. PostgreSQL: `VACUUM` / размер БД, если `MediaAsset` разросся (BLOB).

### Галерея генераций: картинки не появляются

**Симптомы:** SD отработал, в чате картинки есть, `/gallery` пустая или без новых.

1. Ingest после SD должен писать `gallery_kind=generation` (не `chat`).
2. Проверить БД: `SELECT id, gallery_kind, original_name FROM media_assets ORDER BY created_at DESC LIMIT 5;`
3. Старые записи `gallery_kind=chat` с SD metadata — миграция при старте (`migrate.py`) переводит в `generation`.
4. WS `gallery_update` с `kind=generation` — обновление сетки без F5.

### Галерея загрузок и размер БД

- **Галерея загрузок** (`/gallery/uploads`): BLOB шифруются per-user (`users.media_token`); рост БД ≈ сумма загрузок всех пользователей, **без автоудаления**.
- Мониторинг: размер PostgreSQL (`pg_database_size`) и число строк `media_assets` с `gallery_kind=upload`.
- Квота в config пока не задана; при переполнении — удаление через UI или admin purge по пользователю.

---

## Ротация `media_token` (галерея загрузок)

**Симптомы:** после смены/потери токена изображения в `/gallery/uploads` не открываются (decrypt error), `has_media_token` в `/api/auth/me` — `false`.

1. **Потеря токена без бэкапа:** расшифровать старые upload-активы **невозможно**. Восстановление только из бэкапа БД с прежним `users.media_token`.
2. **Плановая ротация (ещё нет API):** остановить web-chat → бэкап БД → для каждого пользователя: сгенерировать новый `media_token`, **перешифровать** все `gallery_kind=upload` (и при необходимости generation) — фоновый скрипт:
   ```bash
   cd /root/web-chat && source .venv/bin/activate
   python -m app.scripts.reencrypt_gallery_assets --batch 50
   ```
   Скрипт переводит только `encryption_version=0` → `1` (legacy plaintext). Upload-записи уже зашифрованы при создании.
3. Перед миграцией схемы: снимок по [DATABASE-BACKUP.md](../deploy/DATABASE-BACKUP.md) (`./scripts/backup-database.sh`).
4. `GET /api/auth/me` отдаёт **`has_media_token`**, не сам токен — токен хранить только в БД и в защищённом бэкапе.

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

Не совмещайте **backup** и **restore** в один момент. Restore останавливает приложение через `systemctl stop web-chat` (fallback `pkill` только без systemd).

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

> Не обязательно для одного пользователя в домашней сети — см. [HANDBOOK §0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки).

1. Шаблон: [deploy/nginx-web-chat.conf.template](../deploy/nginx-web-chat.conf.template) — upstream на `127.0.0.1:8090`, WebSocket upgrade.
2. TLS: Let's Encrypt или свой сертификат; в `.env`: `AUTH_COOKIE_SECURE=true`, `WEB_CHAT_ENV=production`.
3. `TRUSTED_PROXY_IPS=127.0.0.1`, `TRUSTED_WS_ORIGINS=https://<ваш-хост>`.
4. Опционально: Basic Auth в nginx **или** `API_ACCESS_KEY` в приложении — [SECURITY.md](SECURITY.md).
5. `nginx -t && systemctl reload nginx` → проверка чата и `/health` по HTTPS.
