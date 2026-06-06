#!/usr/bin/env bash
# Production: бэкап БД + опционально generated/uploads (см. scripts/backup-all.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WEB_CHAT_ROOT="${WEB_CHAT_ROOT:-${ROOT}}"
export WEB_CHAT_DB_BACKUP_DIR="${WEB_CHAT_DB_BACKUP_DIR:-/var/backups/web-chat/database}"
export WEB_CHAT_BACKUP_GENERATED="${WEB_CHAT_BACKUP_GENERATED:-0}"
export WEB_CHAT_BACKUP_UPLOADS="${WEB_CHAT_BACKUP_UPLOADS:-0}"
exec "${ROOT}/scripts/backup-all.sh" "$@"
