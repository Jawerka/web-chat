# shellcheck shell=bash
# Сборка единого архива бэкапа БД (manifest + tar.gz).

# shellcheck source=scripts/lib/load-dotenv.sh
source "$(dirname "${BASH_SOURCE[0]}")/load-dotenv.sh"
# shellcheck source=scripts/lib/backup-paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/backup-paths.sh"
# shellcheck source=scripts/lib/backup-sqlite.sh
source "$(dirname "${BASH_SOURCE[0]}")/backup-sqlite.sh"
# shellcheck source=scripts/lib/backup-postgres.sh
source "$(dirname "${BASH_SOURCE[0]}")/backup-postgres.sh"

# Создать tar.gz в DEST_DIR. В stdout — путь к архиву.
create_database_backup_archive() {
  local dest_dir="${1:-${WEB_CHAT_DB_BACKUP_DIR}}"
  local stamp="${2:-$(date -u +%Y%m%dT%H%M%SZ)}"
  local dump_format="${WEB_CHAT_PG_DUMP_FORMAT:-custom}"
  local include_generated="${WEB_CHAT_BACKUP_GENERATED:-0}"
  local include_uploads="${WEB_CHAT_BACKUP_UPLOADS:-0}"

  if [[ "${WEB_CHAT_DB_BACKEND}" == "unknown" || -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL не задан в .env" >&2
    return 1
  fi

  mkdir -p "${dest_dir}"

  local tmp
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap 'rm -rf "${tmp}"' RETURN

  local sqlite_list="" backend="${WEB_CHAT_DB_BACKEND}"
  local pg_dump_path="" pg_dump_format="${dump_format}"
  local site_generated=0 site_uploads=0 backup_type="database"

  echo "Бэкап (${stamp}), backend=${backend}…" >&2

  if [[ "${backend}" == "postgresql" ]]; then
    backup_postgres_to "${tmp}/data/postgres" || return 1
    if [[ "${dump_format}" == "plain" ]]; then
      pg_dump_path="data/postgres/web_chat.sql.gz"
    else
      pg_dump_path="data/postgres/web_chat.dump"
    fi
  elif [[ "${backend}" == "sqlite" ]]; then
    if ! backup_sqlite_files_to "${tmp}/data/db"; then
      echo "Нет SQLite в data/db/" >&2
      return 1
    fi
    sqlite_list="$(find "${tmp}/data/db" -maxdepth 1 -name '*.sqlite' -printf '%f\n' 2>/dev/null | paste -sd, - || true)"
  else
    echo "Неподдерживаемый DATABASE_URL: ${DATABASE_URL}" >&2
    return 1
  fi

  if [[ "${include_generated}" == "1" && -d "${WEB_CHAT_ROOT}/data/generated" ]]; then
    mkdir -p "${tmp}/data"
    cp -a "${WEB_CHAT_ROOT}/data/generated" "${tmp}/data/generated"
    site_generated=1
    echo "  + data/generated" >&2
  fi
  if [[ "${include_uploads}" == "1" && -d "${WEB_CHAT_ROOT}/data/uploads" ]]; then
    mkdir -p "${tmp}/data"
    cp -a "${WEB_CHAT_ROOT}/data/uploads" "${tmp}/data/uploads"
    site_uploads=1
    echo "  + data/uploads" >&2
  fi
  if [[ "${site_generated}" == "1" || "${site_uploads}" == "1" ]]; then
    backup_type="full"
  fi

  local hostname manifest
  hostname="$(hostname 2>/dev/null || echo unknown)"
  manifest="${tmp}/manifest.json"

  python3 - "${manifest}" "${stamp}" "${hostname}" "${WEB_CHAT_ROOT}" "${backend}" \
    "${backup_type}" "${sqlite_list}" "${pg_dump_path}" "${pg_dump_format}" \
    "${site_generated}" "${site_uploads}" <<'PY'
import json
import sys

(
    path,
    stamp,
    host,
    root,
    backend,
    backup_type,
    sqlite_csv,
    pg_dump,
    pg_fmt,
    site_gen,
    site_upl,
) = sys.argv[1:]
sqlite_files = [x for x in sqlite_csv.split(",") if x]
payload = {
    "app": "web-chat",
    "backup_version": 2,
    "backup_type": backup_type,
    "created_at_utc": stamp,
    "hostname": host,
    "root": root,
    "archive_format": "tar.gz",
    "database_backend": backend,
    "sqlite_files": sqlite_files,
    "site_files": {
        "generated": site_gen == "1",
        "uploads": site_upl == "1",
    },
}
if backend == "postgresql":
    payload["postgres_dump"] = pg_dump or "data/postgres/web_chat.dump"
    payload["postgres_dump_format"] = pg_fmt or "custom"
open(path, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
PY

  local archive_name archive_path
  archive_name="$(backup_archive_name "${stamp}")"
  archive_path="${dest_dir}/${archive_name}"

  tar -czf "${archive_path}" -C "${tmp}" .
  echo "Архив: ${archive_path} ($(wc -c <"${archive_path}") bytes)" >&2
  printf '%s\n' "${archive_path}"
}
