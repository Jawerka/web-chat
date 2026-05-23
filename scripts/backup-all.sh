#!/usr/bin/env bash
# Резервная копия: база данных (data/backups/database/, ротация 3) + опционально файлы сайта.
#
#   ./scripts/backup-all.sh
#
# Только БД: ./scripts/backup-database.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WEB_CHAT_ROOT="${WEB_CHAT_ROOT:-${ROOT}}"

INCLUDE_GENERATED="${WEB_CHAT_BACKUP_GENERATED:-0}"
INCLUDE_UPLOADS="${WEB_CHAT_BACKUP_UPLOADS:-0}"

echo "=== Бэкап базы данных ==="
"${ROOT}/scripts/backup-database.sh"

if [[ "${INCLUDE_GENERATED}" != "1" && "${INCLUDE_UPLOADS}" != "1" ]]; then
  exit 0
fi

# shellcheck source=scripts/lib/backup-paths.sh
source "${ROOT}/scripts/lib/backup-paths.sh"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SITE_DIR="${WEB_CHAT_SITE_BACKUP_DIR}"
mkdir -p "${SITE_DIR}"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

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

SITE_ARCHIVE="${SITE_DIR}/web-chat-site-${STAMP}.tar.gz"
tar -czf "${SITE_ARCHIVE}" -C "${TMP}" .
echo ""
echo "Архив файлов: ${SITE_ARCHIVE}"
