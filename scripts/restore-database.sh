#!/usr/bin/env bash
# Восстановление БД из архива в data/backups/database/.
#
#   ./scripts/restore-database.sh              # последний бэкап
#   ./scripts/restore-database.sh --list
#   ./scripts/restore-database.sh --index 2    # 2-й по новизне
#   ./scripts/restore-database.sh --stamp 20260523T120000Z
#   ./scripts/restore-database.sh --file /path/to/web-chat-db-….tar.gz
#   ./scripts/restore-database.sh --yes        # без подтверждения
#
# Перед восстановлением остановите web-chat (systemctl stop web-chat).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WEB_CHAT_ROOT="${WEB_CHAT_ROOT:-${ROOT}}"

# shellcheck source=scripts/lib/load-dotenv.sh
source "${WEB_CHAT_ROOT}/scripts/lib/load-dotenv.sh"
# shellcheck source=scripts/lib/backup-paths.sh
source "${WEB_CHAT_ROOT}/scripts/lib/backup-paths.sh"
# shellcheck source=scripts/lib/backup-rotate.sh
source "${WEB_CHAT_ROOT}/scripts/lib/backup-rotate.sh"
# shellcheck source=scripts/lib/backup-database-core.sh
source "${WEB_CHAT_ROOT}/scripts/lib/backup-database-core.sh"

ASSUME_YES=0
PICK_INDEX=1
PICK_STAMP=""
PICK_FILE=""
DO_LIST=0
SAFETY_BACKUP=1
RESTORE_LEGACY_SQLITE=0

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  printf '\nОпции:\n'
  printf '  --list              список архивов\n'
  printf '  --index N           N-й бэкап (1 = новейший)\n'
  printf '  --stamp STAMP       web-chat-db-STAMP.tar.gz\n'
  printf '  --file PATH         явный путь к архиву\n'
  printf '  --yes               без подтверждения\n'
  printf '  --no-safety-backup  не делать бэкап перед восстановлением\n'
  printf '  --legacy-sqlite     также восстановить legacy SQLite из архива\n'
  printf '  -h, --help\n'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) DO_LIST=1; shift ;;
    --index) PICK_INDEX="$2"; shift 2 ;;
    --stamp) PICK_STAMP="$2"; shift 2 ;;
    --file) PICK_FILE="$2"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    --no-safety-backup) SAFETY_BACKUP=0; shift ;;
    --legacy-sqlite) RESTORE_LEGACY_SQLITE=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "Неизвестный аргумент: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$DO_LIST" == "1" ]]; then
  backup_print_list "${WEB_CHAT_DB_BACKUP_DIR}"
  exit 0
fi

_resolve_archive() {
  if [[ -n "$PICK_FILE" ]]; then
    [[ -f "$PICK_FILE" ]] || {
      echo "Файл не найден: $PICK_FILE" >&2
      exit 1
    }
    printf '%s\n' "$PICK_FILE"
    return
  fi
  if [[ -n "$PICK_STAMP" ]]; then
    local p="${WEB_CHAT_DB_BACKUP_DIR}/$(backup_archive_name "${PICK_STAMP}")"
    [[ -f "$p" ]] || {
      echo "Архив не найден: $p" >&2
      exit 1
    }
    printf '%s\n' "$p"
    return
  fi
  local archives=()
  local path
  while IFS= read -r path; do
    [[ -n "$path" ]] && archives+=("$path")
  done < <(backup_list_archives "${WEB_CHAT_DB_BACKUP_DIR}")
  if [[ ${#archives[@]} -eq 0 ]]; then
    echo "Нет бэкапов в ${WEB_CHAT_DB_BACKUP_DIR}" >&2
    echo "Создайте: ./scripts/backup-database.sh" >&2
    exit 1
  fi
  if [[ "$PICK_INDEX" -lt 1 || "$PICK_INDEX" -gt ${#archives[@]} ]]; then
    echo "Неверный --index ${PICK_INDEX} (доступно: ${#archives[@]})" >&2
    exit 1
  fi
  printf '%s\n' "${archives[$((PICK_INDEX - 1))]}"
}

ARCHIVE="$(_resolve_archive)"
echo "Архив: ${ARCHIVE}"

if [[ "$ASSUME_YES" != "1" ]]; then
  echo ""
  echo "ВНИМАНИЕ: будут перезаписаны данные активной БД (${WEB_CHAT_DB_BACKEND} из .env)."
  echo "Остановите сервис: systemctl stop web-chat"
  read -r -p "Продолжить распаковку и восстановление? [y/N] " ans
  case "${ans,,}" in
    y | yes) ;;
    *) echo "Отменено."; exit 0 ;;
  esac
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
tar -xzf "${ARCHIVE}" -C "${TMP}"

MANIFEST="${TMP}/manifest.json"
[[ -f "$MANIFEST" ]] || {
  echo "В архиве нет manifest.json" >&2
  exit 1
}

read_manifest() {
  python3 - "$MANIFEST" <<'PY'
import json
import sys

m = json.load(open(sys.argv[1], encoding="utf-8"))
print(m.get("database_backend", ""))
print(m.get("postgres_dump_format", "custom"))
print(m.get("postgres_dump", ""))
print(",".join(m.get("sqlite_files") or []))
print("1" if m.get("legacy_sqlite_included") else "0")
PY
}

mapfile -t _MF < <(read_manifest)
BACKEND="${_MF[0]}"
PG_FMT="${_MF[1]}"
PG_DUMP_REL="${_MF[2]}"
SQLITE_CSV="${_MF[3]}"
LEGACY_IN_ARCHIVE="${_MF[4]}"

echo "Бэкап: backend=${BACKEND}, stamp из manifest"

_stop_web_chat() {
  local stopped=0
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active web-chat >/dev/null 2>&1; then
    echo "Останавливаю web-chat (systemctl stop web-chat)…" >&2
    systemctl stop web-chat || true
    stopped=1
  fi
  if [[ "$stopped" -eq 0 ]] && pgrep -f 'uvicorn app.main:app' >/dev/null 2>&1; then
    echo "web-chat не через systemd — завершаю uvicorn (fallback pkill)…" >&2
    pkill -f 'uvicorn app.main:app' 2>/dev/null || true
  fi
  sleep 2
}

_safety_backup() {
  [[ "$SAFETY_BACKUP" == "1" ]] || return 0
  echo "Бэкап текущего состояния перед восстановлением…" >&2
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-pre-restore"
  create_database_backup_archive "${WEB_CHAT_DB_BACKUP_DIR}" "${stamp}" >/dev/null
  backup_rotate "${WEB_CHAT_DB_BACKUP_DIR}" "${WEB_CHAT_DB_BACKUP_KEEP}"
}

_restore_postgres() {
  local dump_path="${TMP}/${PG_DUMP_REL}"
  [[ -f "$dump_path" ]] || {
    echo "В архиве нет ${PG_DUMP_REL}" >&2
    exit 1
  }
  if [[ "${WEB_CHAT_DB_BACKEND}" != "postgresql" ]]; then
    echo "В .env не PostgreSQL — смените DATABASE_URL или восстановите только SQLite (--legacy-sqlite)." >&2
    exit 1
  fi
  if ! command -v pg_restore >/dev/null 2>&1 && [[ "${PG_FMT}" != "plain" ]]; then
    echo "Нужен pg_restore (postgresql-client)" >&2
    exit 1
  fi

  # shellcheck disable=SC1090
  eval "$("${WEB_CHAT_ROOT}/.venv/bin/python" -c "
from app.db.pg_cli import shell_exports
from app.config import settings
print(shell_exports(settings.database_url))
")"

  echo "Восстановление PostgreSQL (${PGDATABASE})…" >&2
  PGPASSWORD="${PGPASSWORD}" psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '${PGDATABASE}' AND pid <> pg_backend_pid();
SQL

  dropdb -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" --if-exists "${PGDATABASE}"
  createdb -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -O "${PGUSER}" "${PGDATABASE}"

  if [[ "${PG_FMT}" == "plain" ]]; then
    gunzip -c "${dump_path}" | PGPASSWORD="${PGPASSWORD}" psql -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" -v ON_ERROR_STOP=1
  else
    pg_restore -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" \
      --no-owner --no-acl "${dump_path}"
  fi
}

_restore_sqlite_files() {
  local src_dir="${TMP}/data/db"
  local dest_dir="${WEB_CHAT_ROOT}/data/db"
  [[ -d "$src_dir" ]] || {
    echo "В архиве нет data/db/" >&2
    exit 1
  }
  if [[ "${WEB_CHAT_DB_BACKEND}" != "sqlite" ]]; then
    echo "В .env не SQLite — для отката смените DATABASE_URL на sqlite+aiosqlite://…" >&2
    exit 1
  fi
  mkdir -p "${dest_dir}"
  local f name
  shopt -s nullglob
  for f in "${src_dir}"/*.sqlite; do
    name="$(basename "$f")"
    if [[ -f "${dest_dir}/${name}" ]]; then
      cp -a "${dest_dir}/${name}" "${dest_dir}/${name}.before-restore"
    fi
    cp -a "$f" "${dest_dir}/${name}"
    echo "  SQLite: ${name}" >&2
  done
  shopt -u nullglob
  chmod 644 "${dest_dir}"/*.sqlite 2>/dev/null || true
}

_restore_legacy_sqlite() {
  local src_dir="${TMP}/data/db"
  [[ -d "$src_dir" ]] || return 0
  mkdir -p "${WEB_CHAT_ROOT}/data/db"
  local f
  for f in "${src_dir}"/*.sqlite; do
    [[ -f "$f" ]] || continue
    cp -a "$f" "${WEB_CHAT_ROOT}/data/db/$(basename "$f")"
    echo "  legacy SQLite → data/db/$(basename "$f")" >&2
  done
}

_stop_web_chat
_safety_backup

if [[ "${WEB_CHAT_DB_BACKEND}" == "postgresql" && "${BACKEND}" == "postgresql" ]]; then
  _restore_postgres
elif [[ "${WEB_CHAT_DB_BACKEND}" == "sqlite" && "${BACKEND}" == "sqlite" ]]; then
  _restore_sqlite_files
else
  echo "Несовпадение: бэкап=${BACKEND}, .env=${WEB_CHAT_DB_BACKEND}" >&2
  echo "Приведите DATABASE_URL в соответствие с типом бэкапа." >&2
  exit 1
fi

if [[ "$RESTORE_LEGACY_SQLITE" == "1" && "$LEGACY_IN_ARCHIVE" == "1" ]]; then
  _restore_legacy_sqlite
fi

_post_restore_verify() {
  echo ""
  echo "Проверка БД после restore…" >&2
  if ! "${WEB_CHAT_ROOT}/.venv/bin/python" - <<'PY' 2>&1; then
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings


async def main() -> None:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            one = (await conn.execute(text("SELECT 1"))).scalar_one()
            if one != 1:
                raise RuntimeError(f"unexpected SELECT 1 → {one!r}")
    finally:
        await engine.dispose()
    print("  OK: подключение к БД, SELECT 1", file=sys.stderr)


asyncio.run(main())
PY
    echo "Проверка БД не удалась." >&2
    return 1
  fi
  if [[ "${WEB_CHAT_DB_BACKEND}" == "postgresql" ]]; then
    if "${WEB_CHAT_ROOT}/.venv/bin/python" -m alembic current 2>&1 | head -5; then
      echo "  OK: alembic current" >&2
    else
      echo "  Предупреждение: alembic current не выполнен (запустите сервис — upgrade при старте)" >&2
    fi
  fi
  return 0
}

_post_restore_verify || exit 1

echo ""
echo "Восстановление завершено."
echo "Запуск: systemctl start web-chat"
echo "После старта: curl -sS http://127.0.0.1:8099/api/health | head -c 200"
