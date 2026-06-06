# База данных web-chat

## Production

**PostgreSQL** — задаётся в `.env`:

```env
DATABASE_URL=postgresql+asyncpg://webchat:…@127.0.0.1:5432/web_chat
```

Миграции схемы: Alembic (`alembic upgrade head` при старте приложения).

## Резервное копирование и восстановление

Каталог: **`data/backups/database/`** (см. [deploy/DATABASE-BACKUP.md](../../deploy/DATABASE-BACKUP.md)).

```bash
./scripts/backup-database.sh
./scripts/restore-database.sh --list
./scripts/restore-database.sh --yes    # после systemctl stop web-chat
```

После restore скрипт выполняет `SELECT 1` и `alembic current` (Postgres).

## SQLite (только dev / тесты)

Для локальной разработки без Postgres можно указать в `.env`:

```env
DATABASE_URL=sqlite+aiosqlite:///./data/db/web_chat.sqlite
```

Файл создаётся при первом запуске. Legacy-снимок production SQLite (cutover 2026-05-23) с диска удалён; восстановление — только из архивов `scripts/restore-database.sh`.

Перенос из внешнего файла SQLite в Postgres: `python -m app.scripts.migrate_sqlite_to_postgres --source sqlite+aiosqlite:///path/to/file.sqlite`.
