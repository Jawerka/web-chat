#!/usr/bin/env bash
# Резервная копия: один архив web-chat-db-*.tar.gz (БД + опционально generated/uploads).
#
#   ./scripts/backup-all.sh
#   WEB_CHAT_BACKUP_GENERATED=1 WEB_CHAT_BACKUP_UPLOADS=1 ./scripts/backup-all.sh
#
# Только БД: ./scripts/backup-database.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WEB_CHAT_ROOT="${WEB_CHAT_ROOT:-${ROOT}}"
export WEB_CHAT_BACKUP_GENERATED="${WEB_CHAT_BACKUP_GENERATED:-0}"
export WEB_CHAT_BACKUP_UPLOADS="${WEB_CHAT_BACKUP_UPLOADS:-0}"

exec "${ROOT}/scripts/backup-database.sh"
