#!/usr/bin/env bash
# Обёртка для production: бэкап в /var/backups/web-chat (см. scripts/backup-all.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WEB_CHAT_ROOT="${WEB_CHAT_ROOT:-${ROOT}}"
export WEB_CHAT_BACKUP_DIR="${WEB_CHAT_BACKUP_DIR:-/var/backups/web-chat}"
export WEB_CHAT_BACKUP_FORMAT="${WEB_CHAT_BACKUP_FORMAT:-tar.gz}"

exec "${ROOT}/scripts/backup-all.sh"
