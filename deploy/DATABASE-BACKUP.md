# Резервное копирование и восстановление БД

Единый каталог: **`data/backups/database/`** (на сервере: `WEB_CHAT_DB_BACKUP_DIR`, например `/var/backups/web-chat/database`).

Каждый запуск создаёт **один** архив `web-chat-db-<UTC-stamp>.tar.gz`. Хранится не более **3** архивов (ротация при каждом новом бэкапе). Старые имена `web-chat-backup-*` / `web-chat-pg-backup-*` по-прежнему видны в `--list` для восстановления.

## Бэкап

```bash
# Только PostgreSQL (production)
./scripts/backup-database.sh

# БД + файлы на диске (generated, uploads) в том же архиве
WEB_CHAT_BACKUP_GENERATED=1 WEB_CHAT_BACKUP_UPLOADS=1 ./scripts/backup-all.sh
```

Production:

```bash
./deploy/backup-database.sh
# полный (БД + файлы в одном архиве):
WEB_CHAT_BACKUP_GENERATED=1 WEB_CHAT_BACKUP_UPLOADS=1 ./deploy/backup-data.sh
```

Переменные:

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `WEB_CHAT_DB_BACKUP_DIR` | `data/backups/database` | Каталог архивов |
| `WEB_CHAT_DB_BACKUP_KEEP` | `3` | Сколько архивов хранить |
| `WEB_CHAT_PG_DUMP_FORMAT` | `custom` | `custom` (`.dump`) или `plain` (`.sql.gz`) |
| `WEB_CHAT_BACKUP_GENERATED` | `0` | Включить `data/generated/` в архив |
| `WEB_CHAT_BACKUP_UPLOADS` | `0` | Включить `data/uploads/` в архив |

Cron (ежедневно в 03:00):

```cron
0 3 * * * root cd /opt/web-chat && ./scripts/backup-database.sh >> /var/log/web-chat-backup.log 2>&1
```

## Восстановление

**Остановите приложение** перед восстановлением.

```bash
systemctl stop web-chat

./scripts/restore-database.sh --list
./scripts/restore-database.sh --yes
./scripts/restore-database.sh --index 2 --yes
./scripts/restore-database.sh --stamp 20260523T120000Z --yes
```

Перед восстановлением создаётся **страховочный бэкап** текущей БД (если не указано `--no-safety-backup`).

`restore-database.sh` восстанавливает **только базу данных**. Файлы `data/generated/` и `data/uploads/` из полного архива (`backup_type: full`) при необходимости распакуйте вручную:

```bash
tar -xzf web-chat-db-….tar.gz -C /tmp/restore-inspect data/generated data/uploads
```

## Содержимое архива (manifest v2)

```json
{
  "app": "web-chat",
  "backup_version": 2,
  "backup_type": "database",
  "database_backend": "postgresql",
  "postgres_dump": "data/postgres/web_chat.dump",
  "postgres_dump_format": "custom",
  "site_files": { "generated": false, "uploads": false }
}
```

Файлы:

```
manifest.json
data/postgres/web_chat.dump     # PostgreSQL (custom)
data/db/*.sqlite                # только при backend=sqlite в .env
data/generated/                 # опционально (backup_type=full)
data/uploads/                   # опционально (backup_type=full)
```

## Эксплуатация

- Не запускайте `backup-database.sh` и `restore-database.sh` одновременно.
- Перед restore скрипт останавливает `web-chat` через systemctl.
- **Postgres:** на production предпочитайте `~/.pgpass` вместо пароля в окружении.

## См. также

- [POSTGRES.md](POSTGRES.md) — установка Postgres, ETL
- [DEPLOY.md](DEPLOY.md) — общая эксплуатация
- [data/db/README.md](../data/db/README.md) — dev SQLite
