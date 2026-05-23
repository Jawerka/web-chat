#!/usr/bin/env bash
# Production: восстановление БД из WEB_CHAT_DB_BACKUP_DIR.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WEB_CHAT_ROOT="${WEB_CHAT_ROOT:-${ROOT}}"
export WEB_CHAT_DB_BACKUP_DIR="${WEB_CHAT_DB_BACKUP_DIR:-/var/backups/web-chat/database}"
exec "${ROOT}/scripts/restore-database.sh" "$@"
