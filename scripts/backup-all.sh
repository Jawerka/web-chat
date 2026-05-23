#!/usr/bin/env bash
# Резервная копия всех SQLite БД web-chat в один архив (ZIP по умолчанию).
#
# Ручной запуск из корня проекта:
#   ./scripts/backup-all.sh
#
# Переменные окружения:
#   WEB_CHAT_ROOT              — корень проекта (по умолчанию: родитель scripts/)
#   WEB_CHAT_BACKUP_DIR        — каталог для архивов (по умолчанию: data/backups)
#   WEB_CHAT_BACKUP_FORMAT     — zip | tar.gz | 7z (по умолчанию: zip)
#   WEB_CHAT_BACKUP_GENERATED  — 1 = включить data/generated
#   WEB_CHAT_BACKUP_UPLOADS    — 1 = включить data/uploads
#
# Пример cron (позже):
#   0 3 * * * cd /opt/web-chat && ./scripts/backup-all.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${WEB_CHAT_BACKUP_DIR:-${ROOT}/data/backups}"
FORMAT="${WEB_CHAT_BACKUP_FORMAT:-zip}"
INCLUDE_GENERATED="${WEB_CHAT_BACKUP_GENERATED:-0}"
INCLUDE_UPLOADS="${WEB_CHAT_BACKUP_UPLOADS:-0}"

DB_DIR="${ROOT}/data/db"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_BASE="web-chat-backup-${STAMP}"

mkdir -p "${DEST_DIR}"

if [[ ! -d "${DB_DIR}" ]]; then
  echo "Нет каталога БД: ${DB_DIR}" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

mkdir -p "${TMP}/data/db"

shopt -s nullglob
DB_FILES=("${DB_DIR}"/*.sqlite)
shopt -u nullglob

if [[ ${#DB_FILES[@]} -eq 0 ]]; then
  echo "Нет файлов *.sqlite в ${DB_DIR}" >&2
  exit 1
fi

HAS_SQLITE3=0
if command -v sqlite3 >/dev/null 2>&1; then
  HAS_SQLITE3=1
fi

for DB_PATH in "${DB_FILES[@]}"; do
  NAME="$(basename "${DB_PATH}")"
  DEST_DB="${TMP}/data/db/${NAME}"
  if [[ "${HAS_SQLITE3}" -eq 1 ]]; then
    sqlite3 "${DB_PATH}" ".backup '${DEST_DB}'"
    echo "  SQLite backup: ${NAME}"
  else
    cp -a "${DB_PATH}" "${DEST_DB}"
    echo "  WARN: sqlite3 не найден — скопирован ${NAME} (лучше установить sqlite3)" >&2
  fi
done

if [[ "${INCLUDE_GENERATED}" == "1" && -d "${ROOT}/data/generated" ]]; then
  mkdir -p "${TMP}/data"
  cp -a "${ROOT}/data/generated" "${TMP}/data/generated"
  echo "  + data/generated"
fi

if [[ "${INCLUDE_UPLOADS}" == "1" && -d "${ROOT}/data/uploads" ]]; then
  mkdir -p "${TMP}/data"
  cp -a "${ROOT}/data/uploads" "${TMP}/data/uploads"
  echo "  + data/uploads"
fi

HOSTNAME="$(hostname 2>/dev/null || echo unknown)"
MANIFEST="${TMP}/manifest.json"
python3 - "${MANIFEST}" "${STAMP}" "${HOSTNAME}" "${ROOT}" "${FORMAT}" \
  "${INCLUDE_GENERATED}" "${INCLUDE_UPLOADS}" "${DB_FILES[@]}" <<'PY'
import json
import sys

path, stamp, host, root, fmt, inc_gen, inc_upl, *dbs = sys.argv[1:]
payload = {
    "app": "web-chat",
    "created_at_utc": stamp,
    "hostname": host,
    "root": root,
    "format": fmt,
    "databases": [__import__("os").path.basename(d) for d in dbs],
    "include_generated": inc_gen == "1",
    "include_uploads": inc_upl == "1",
}
open(path, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
PY

_pack_zip() {
  local out="$1"
  python3 - "${TMP}" "${out}" <<'PY'
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
out = Path(sys.argv[2])
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            zf.write(path, path.relative_to(root).as_posix())
print(f"ZIP: {out} ({out.stat().st_size} bytes)")
PY
}

_pack_tar() {
  local out="$1"
  tar -czf "${out}" -C "${TMP}" .
  echo "tar.gz: ${out} ($(wc -c < "${out}") bytes)"
}

_pack_7z() {
  local out="$1"
  if ! command -v 7z >/dev/null 2>&1; then
    echo "7z не найден. Установите p7zip-full или используйте WEB_CHAT_BACKUP_FORMAT=zip" >&2
    exit 1
  fi
  rm -f "${out}"
  (cd "${TMP}" && 7z a -t7z -mx=5 "${out}" . >/dev/null)
  echo "7z: ${out} ($(wc -c < "${out}") bytes)"
}

echo "Резервное копирование web-chat (${STAMP})…"
case "${FORMAT}" in
  zip)
    ARCHIVE="${DEST_DIR}/${ARCHIVE_BASE}.zip"
    _pack_zip "${ARCHIVE}"
    ;;
  tar.gz | tgz)
    ARCHIVE="${DEST_DIR}/${ARCHIVE_BASE}.tar.gz"
    _pack_tar "${ARCHIVE}"
    ;;
  7z)
    ARCHIVE="${DEST_DIR}/${ARCHIVE_BASE}.7z"
    _pack_7z "${ARCHIVE}"
    ;;
  *)
    echo "Неизвестный формат: ${FORMAT} (zip | tar.gz | 7z)" >&2
    exit 1
    ;;
esac

echo "Готово: ${ARCHIVE}"
echo "Скопируйте архив в безопасное место (NAS, другой диск, облако)."
