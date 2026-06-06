# shellcheck shell=bash
# pg_dump в каталог data/postgres/ (для backup-all или отдельного архива).

backup_postgres_to() {
  local dest_pg_dir="$1"
  local dump_format="${WEB_CHAT_PG_DUMP_FORMAT:-custom}"

  if [[ "${WEB_CHAT_DB_BACKEND}" != "postgresql" ]]; then
    echo "backup_postgres_to: не PostgreSQL backend" >&2
    return 1
  fi
  if ! command -v pg_dump >/dev/null 2>&1; then
    echo "postgresql-client (pg_dump) не установлен" >&2
    return 1
  fi

  mkdir -p "${dest_pg_dir}"

  # shellcheck disable=SC1090
  eval "$("${WEB_CHAT_ROOT}/.venv/bin/python" -c "
from app.db.pg_cli import shell_exports
from app.config import settings
print(shell_exports(settings.database_url))
")"

  if [[ "${dump_format}" == "plain" ]]; then
    pg_dump -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" \
      --no-owner --no-acl | gzip -9 >"${dest_pg_dir}/web_chat.sql.gz"
    echo "  PostgreSQL plain SQL: web_chat.sql.gz"
  else
    pg_dump -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" \
      -Fc --no-owner --no-acl -f "${dest_pg_dir}/web_chat.dump"
    echo "  PostgreSQL custom dump: web_chat.dump"
  fi
  return 0
}
