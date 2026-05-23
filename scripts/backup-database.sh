#!/usr/bin/env bash
# Резервная копия базы данных → data/backups/database/ (ротация: не более 3 архивов).
#
#   ./scripts/backup-database.sh
#   WEB_CHAT_DB_BACKUP_KEEP=5 ./scripts/backup-database.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WEB_CHAT_ROOT="${WEB_CHAT_ROOT:-${ROOT}}"

# shellcheck source=scripts/lib/backup-database-core.sh
source "${WEB_CHAT_ROOT}/scripts/lib/backup-database-core.sh"
# shellcheck source=scripts/lib/backup-rotate.sh
source "${WEB_CHAT_ROOT}/scripts/lib/backup-rotate.sh"

mkdir -p "${WEB_CHAT_DB_BACKUP_DIR}"
# Перенос старых архивов из data/backups/ (если остались после обновления)
_legacy="${WEB_CHAT_ROOT}/data/backups"
for _old in "${_legacy}"/web-chat-backup-*.tar.gz "${_legacy}"/web-chat-pg-backup-*.tar.gz; do
  [[ -f "${_old}" ]] || continue
  mv "${_old}" "${WEB_CHAT_DB_BACKUP_DIR}/" && echo "Перенесён в database/: $(basename "${_old}")"
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$(create_database_backup_archive "${WEB_CHAT_DB_BACKUP_DIR}" "${STAMP}")"
backup_rotate "${WEB_CHAT_DB_BACKUP_DIR}" "${WEB_CHAT_DB_BACKUP_KEEP}"

echo ""
echo "Готово. Восстановление: ./scripts/restore-database.sh"
echo "Список бэкапов:       ./scripts/restore-database.sh --list"
