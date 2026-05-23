# shellcheck shell=bash
# Каталоги и имена архивов бэкапа БД.

: "${WEB_CHAT_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# Единый каталог бэкапов базы данных (в репозитории / на сервере)
WEB_CHAT_DB_BACKUP_DIR="${WEB_CHAT_DB_BACKUP_DIR:-${WEB_CHAT_ROOT}/data/backups/database}"

# Сколько архивов web-chat-db-*.tar.gz хранить
WEB_CHAT_DB_BACKUP_KEEP="${WEB_CHAT_DB_BACKUP_KEEP:-3}"

WEB_CHAT_DB_BACKUP_PREFIX="web-chat-db"
WEB_CHAT_DB_BACKUP_EXT=".tar.gz"

# Опционально: полный бэкап (БД + generated/uploads) — отдельный каталог
WEB_CHAT_SITE_BACKUP_DIR="${WEB_CHAT_SITE_BACKUP_DIR:-${WEB_CHAT_ROOT}/data/backups/site}"

backup_archive_name() {
  local stamp="$1"
  printf '%s-%s%s' "${WEB_CHAT_DB_BACKUP_PREFIX}" "${stamp}" "${WEB_CHAT_DB_BACKUP_EXT}"
}
