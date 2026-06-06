# PostgreSQL + Alembic (P2.1)

LAN по умолчанию остаётся на **SQLite**. Postgres — для multi-instance / v2.

## Зависимости

Уже в `pyproject.toml`: `asyncpg`, `psycopg`, `alembic`.

## `.env`

```env
DATABASE_URL=postgresql+asyncpg://webchat:SECRET@127.0.0.1:5432/web_chat
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
```

Создание БД:

```bash
sudo -u postgres psql -c "CREATE USER webchat WITH PASSWORD 'SECRET';"
sudo -u postgres psql -c "CREATE DATABASE web_chat OWNER webchat;"
```

## Миграции

При старте приложения с Postgres URL выполняется `alembic upgrade head` (см. `app/db/session.py`).

Вручную:

```bash
cd /opt/web-chat   # каталог проекта
source .venv/bin/activate
python -m app.scripts.db_upgrade
# или
alembic upgrade head
```

Новая ревизия:

```bash
alembic revision --autogenerate -m "описание"
alembic upgrade head
```

## Существующая SQLite

Поведение **не меняется**: `create_all` + `app/db/migrate.py`.

Чтобы выровнять ревизию Alembic без пересоздания файла (опционально):

```bash
DATABASE_URL=sqlite+aiosqlite:///./data/db/web_chat.sqlite alembic stamp head
```

## Перенос данных (ETL SQLite → Postgres)

Скрипт: `python -m app.scripts.migrate_sqlite_to_postgres`  
Ядро: `app/db/etl_sqlite_to_postgres.py` — копирует таблицы в порядке FK, сохраняет UUID.

| Таблица | Примечание |
|---------|------------|
| presets, prompt_macros | Сначала |
| conversations, media_assets | media_assets содержит BLOB |
| messages, attachments | В конце |

**Перед записью:** остановите web-chat или работайте с **копией** файла SQLite.

```bash
# Путь к копии SQLite (снимок или бэкап из restore-database.sh --legacy-sqlite)
export SQLITE_SOURCE=sqlite+aiosqlite:///./data/db/web_chat.sqlite.bak
export DATABASE_URL=postgresql+asyncpg://webchat:SECRET@127.0.0.1:5432/web_chat

# Подсчёт строк без записи
python -m app.scripts.migrate_sqlite_to_postgres \
  --source "$SQLITE_SOURCE" \
  --target "$DATABASE_URL" \
  --dry-run

# Полный перенос (очистка приёмника + вставка)
python -m app.scripts.migrate_sqlite_to_postgres \
  --source "$SQLITE_SOURCE" \
  --target "$DATABASE_URL" \
  --truncate-target --yes
```

Флаги:

- `--truncate-target` — `TRUNCATE … CASCADE` на Postgres (нужен для непустого приёмника)
- `--skip-media` — не копировать `media_assets` (меньше объём; картинки в чате не заработают)
- `--batch-size` — по умолчанию 100; для BLOB автоматически ≤ 50
- `--yes` — обязателен для реальной записи (без `--dry-run`)

После успешного ETL: `DATABASE_URL` в `.env` → Postgres, перезапуск сервиса. На приёмнике выполняется `alembic upgrade head` и `stamp head`.

## Завершение cutover (приложение на Postgres)

1. **Сверка:**  
   `python -m app.scripts.verify_migration --target "$DATABASE_URL"` → «ИТОГ: миграция согласована».
2. **`.env`:** `DATABASE_URL=postgresql+asyncpg://…` (см. `.env.example`).
3. **Бэкап:** `./scripts/backup-database.sh` — основной способ отката (см. [data/db/README.md](../data/db/README.md)).
4. **Сервис:** `systemctl enable postgresql web-chat && systemctl restart web-chat`.
5. **Проверка:** `curl -s http://127.0.0.1:8090/api/health`.

Откат: `./scripts/restore-database.sh` или восстановление SQLite из архива (`--legacy-sqlite`), затем смена `DATABASE_URL` в `.env`.

## Резервное копирование и восстановление

См. **[DATABASE-BACKUP.md](DATABASE-BACKUP.md)**.

```bash
./scripts/backup-database.sh
./scripts/restore-database.sh --list
systemctl stop web-chat
./scripts/restore-database.sh --yes
systemctl start web-chat
```

Архивы: `data/backups/database/web-chat-db-*.tar.gz` (не более 3 штук).

Тесты: `tests/test_etl_sqlite_to_postgres.py` (SQLite→SQLite без живого Postgres).
