#!/usr/bin/env bash
# Резервная копия SQLite и (опционально) сгенерированных изображений web-chat.
set -euo pipefail

ROOT="${WEB_CHAT_ROOT:-/root/web-chat}"
DEST="${WEB_CHAT_BACKUP_DIR:-/var/backups/web-chat}"
INCLUDE_GENERATED="${WEB_CHAT_BACKUP_GENERATED:-0}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="${DEST}/web-chat-${STAMP}.tar.gz"

mkdir -p "${DEST}"

DB_DIR="${ROOT}/data/db"
if [[ ! -d "${DB_DIR}" ]]; then
  echo "Нет каталога БД: ${DB_DIR}" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

mkdir -p "${TMP}/data/db"
if command -v sqlite3 >/dev/null 2>&1 && [[ -f "${DB_DIR}/web_chat.sqlite" ]]; then
  sqlite3 "${DB_DIR}/web_chat.sqlite" ".backup '${TMP}/data/db/web_chat.sqlite'"
else
  cp -a "${DB_DIR}/." "${TMP}/data/db/"
fi

if [[ "${INCLUDE_GENERATED}" == "1" && -d "${ROOT}/data/generated" ]]; then
  mkdir -p "${TMP}/data"
  cp -a "${ROOT}/data/generated" "${TMP}/data/generated"
fi

tar -czf "${ARCHIVE}" -C "${TMP}" data
echo "Создан архив: ${ARCHIVE}"
