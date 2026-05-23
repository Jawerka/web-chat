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

## Перенос данных

Отдельный скрипт не входит в P2.1. Рекомендация: экспорт бесед через UI/API и новая БД, либо одноразовый `pgloader` / custom ETL.
