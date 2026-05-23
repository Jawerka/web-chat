#!/usr/bin/env bash
# Production: бэкап БД → /var/backups/web-chat/database (или WEB_CHAT_DB_BACKUP_DIR).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WEB_CHAT_ROOT="${WEB_CHAT_ROOT:-${ROOT}}"
export WEB_CHAT_DB_BACKUP_DIR="${WEB_CHAT_DB_BACKUP_DIR:-/var/backups/web-chat/database}"
exec "${ROOT}/scripts/backup-database.sh" "$@"
