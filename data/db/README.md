# База данных web-chat

## Активная (production)

**PostgreSQL** — задаётся в `.env`:

```env
DATABASE_URL=postgresql+asyncpg://webchat:…@127.0.0.1:5432/web_chat
```

Миграции схемы: Alembic (`alembic upgrade head` при старте приложения).

## Резервная копия SQLite (откат)

Файл **`web_chat.sqlite`** — снимок до перехода на Postgres (только чтение).

| Параметр | Значение |
|----------|----------|
| Путь | `data/db/web_chat.sqlite` |
| Размер | ~2.1 GB (BLOB в media_assets) |
| Дата cutover | 2026-05-23 |

### Откат на SQLite

1. Остановить сервис: `systemctl stop web-chat`
2. В `.env` вернуть:  
   `DATABASE_URL=sqlite+aiosqlite:///./data/db/web_chat.sqlite`
3. `chmod 644 data/db/web_chat.sqlite` (если был read-only)
4. Запустить: `systemctl start web-chat`

### Повторный ETL в Postgres

```bash
source .venv/bin/activate
export MIGRATE_TARGET_URL="$DATABASE_URL"   # Postgres из .env
python -m app.scripts.migrate_sqlite_to_postgres \
  --source sqlite+aiosqlite:///./data/db/web_chat.sqlite \
  --target "$MIGRATE_TARGET_URL" \
  --truncate-target --yes

python -m app.scripts.verify_migration --target "$MIGRATE_TARGET_URL"
```

Сверка: `python -m app.scripts.verify_migration --target "$DATABASE_URL"`

## Резервное копирование и восстановление

Каталог: **`data/backups/database/`** (см. [deploy/DATABASE-BACKUP.md](../../deploy/DATABASE-BACKUP.md)).

```bash
./scripts/backup-database.sh
./scripts/restore-database.sh --list
./scripts/restore-database.sh --yes    # после systemctl stop web-chat
```
