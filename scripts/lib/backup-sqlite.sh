# shellcheck shell=bash
# Резервное копирование файлов SQLite в каталог (для включения в общий архив).

backup_sqlite_files_to() {
  local dest_db_dir="$1"
  local db_dir="${WEB_CHAT_ROOT}/data/db"
  shift

  mkdir -p "${dest_db_dir}"

  shopt -s nullglob
  local db_files=("${db_dir}"/*.sqlite "${db_dir}"/*.sqlite-*)
  shopt -u nullglob

  # Только основные файлы БД (без -wal/-shm в отдельном списке если уже покрыты .sqlite backup)
  local primary=()
  local f
  for f in "${db_dir}"/*.sqlite; do
    [[ -f "$f" ]] && primary+=("$f")
  done

  if [[ ${#primary[@]} -eq 0 ]]; then
    echo "  (нет *.sqlite в ${db_dir})" >&2
    return 1
  fi

  local has_sqlite3=0
  command -v sqlite3 >/dev/null 2>&1 && has_sqlite3=1

  for db_path in "${primary[@]}"; do
    local name dest_db
    name="$(basename "${db_path}")"
    dest_db="${dest_db_dir}/${name}"
    if [[ "${has_sqlite3}" -eq 1 ]]; then
      sqlite3 "${db_path}" ".backup '${dest_db}'"
      echo "  SQLite backup: ${name}"
    else
      cp -a "${db_path}" "${dest_db}"
      echo "  WARN: sqlite3 не найден — скопирован ${name}" >&2
    fi
  done
  return 0
}
