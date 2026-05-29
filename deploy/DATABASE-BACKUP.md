# Резервное копирование и восстановление БД

Единый каталог: **`data/backups/database/`** (на сервере можно задать `WEB_CHAT_DB_BACKUP_DIR`, например `/var/backups/web-chat/database`).

Хранится **не более 3** архивов `web-chat-db-<UTC-stamp>.tar.gz` (ротация при каждом новом бэкапе). Старые имена `web-chat-backup-*` / `web-chat-pg-backup-*` тоже учитываются.

## Бэкап

```bash
# Только база данных (рекомендуется)
./scripts/backup-database.sh

# БД + опционально generated/uploads → data/backups/site/
WEB_CHAT_BACKUP_GENERATED=1 WEB_CHAT_BACKUP_UPLOADS=1 ./scripts/backup-all.sh
```

Production:

```bash
./deploy/backup-database.sh
# или полный (БД + файлы):
./deploy/backup-data.sh
```

Переменные:

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `WEB_CHAT_DB_BACKUP_DIR` | `data/backups/database` | Каталог архивов БД |
| `WEB_CHAT_DB_BACKUP_KEEP` | `3` | Сколько архивов хранить |
| `WEB_CHAT_PG_DUMP_FORMAT` | `custom` | `custom` (`.dump`) или `plain` (`.sql.gz`) |
| `WEB_CHAT_BACKUP_LEGACY_SQLITE` | `auto` | При Postgres — положить в архив `data/db/web_chat.sqlite` |

Cron (ежедневно в 03:00):

```cron
0 3 * * * root cd /opt/web-chat && ./scripts/backup-database.sh >> /var/log/web-chat-backup.log 2>&1
```

## Восстановление

**Остановите приложение** перед восстановлением.

```bash
systemctl stop web-chat

# Список архивов (1 = самый новый)
./scripts/restore-database.sh --list

# Последний бэкап (по умолчанию)
./scripts/restore-database.sh --yes

# Конкретный бэкап
./scripts/restore-database.sh --index 2 --yes
./scripts/restore-database.sh --stamp 20260523T120000Z --yes
./scripts/restore-database.sh --file /path/to/web-chat-db-….tar.gz --yes
```

Перед восстановлением создаётся **страховочный бэкап** текущей БД (если не указано `--no-safety-backup`).

Восстановление подстраивается под **`DATABASE_URL` в `.env`**:

- Postgres в `.env` → `pg_restore` / `psql` из архива с `database_backend=postgresql`
- SQLite в `.env` → копирование `data/db/*.sqlite` из архива

Дополнительно: `--legacy-sqlite` — скопировать legacy SQLite из архива в `data/db/` (при работе на Postgres).

После восстановления:

```bash
systemctl start web-chat
python -m app.scripts.verify_migration --target "$DATABASE_URL"   # при сравнении с SQLite
```

## Содержимое архива

```
manifest.json
data/postgres/web_chat.dump    # при Postgres (custom)
data/db/web_chat.sqlite        # опционально (legacy)
```

## Эксплуатация (один оператор)

- **Не запускайте** `backup-database.sh` и `restore-database.sh` **одновременно** — дождитесь завершения одной операции (модель «один оператор» из [HANDBOOK](../HANDBOOK.md)).
- Перед restore скрипт останавливает приложение: сначала `systemctl stop web-chat`, `pkill` uvicorn — только если сервис не под systemd и процесс ещё жив.
- **Postgres:** `PGPASSWORD` может быть виден в `ps` на время `pg_dump`/`psql`. На production предпочитайте `~/.pgpass` (mode `0600`) и экспорт `PGPASSFILE` в cron/unit — см. [документацию libpq](https://www.postgresql.org/docs/current/libpq-pgpass.html).

## См. также

- [POSTGRES.md](POSTGRES.md) — установка Postgres, ETL
- [DEPLOY.md](DEPLOY.md) — общая эксплуатация
- [data/db/README.md](../data/db/README.md) — откат на SQLite
