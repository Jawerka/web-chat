# shellcheck shell=bash
# Ротация архивов БД: оставить не более N последних.

# shellcheck source=scripts/lib/backup-paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/backup-paths.sh"

backup_list_archives() {
  local dir="${1:-${WEB_CHAT_DB_BACKUP_DIR}}"
  [[ -d "$dir" ]] || return 0
  find "$dir" -maxdepth 1 -type f \( \
    -name "${WEB_CHAT_DB_BACKUP_PREFIX}-*.tar.gz" \
    -o -name "web-chat-backup-*.tar.gz" \
    -o -name "web-chat-pg-backup-*.tar.gz" \
    \) 2>/dev/null | sort -r
}

backup_latest_archive() {
  backup_list_archives "${1:-${WEB_CHAT_DB_BACKUP_DIR}}" | head -n 1
}

backup_rotate() {
  local dir="${1:-${WEB_CHAT_DB_BACKUP_DIR}}"
  local keep="${2:-${WEB_CHAT_DB_BACKUP_KEEP}}"
  local archives=()
  local i=0
  local path

  mkdir -p "${dir}"
  while IFS= read -r path; do
    [[ -n "$path" ]] && archives+=("$path")
  done < <(backup_list_archives "${dir}")

  if [[ ${#archives[@]} -le ${keep} ]]; then
    return 0
  fi

  for ((i = keep; i < ${#archives[@]}; i++)); do
    echo "Удалён старый бэкап: $(basename "${archives[i]}")"
    rm -f "${archives[i]}"
  done
}

site_backup_list_archives() {
  local dir="${1:-${WEB_CHAT_SITE_BACKUP_DIR}}"
  [[ -d "$dir" ]] || return 0
  find "$dir" -maxdepth 1 -type f -name 'web-chat-site-*.tar.gz' 2>/dev/null | sort -r
}

site_backup_rotate() {
  local dir="${1:-${WEB_CHAT_SITE_BACKUP_DIR}}"
  local keep="${2:-${WEB_CHAT_SITE_BACKUP_KEEP}}"
  local archives=()
  local i=0
  local path

  mkdir -p "${dir}"
  while IFS= read -r path; do
    [[ -n "$path" ]] && archives+=("$path")
  done < <(site_backup_list_archives "${dir}")

  if [[ ${#archives[@]} -le ${keep} ]]; then
    return 0
  fi

  for ((i = keep; i < ${#archives[@]}; i++)); do
    echo "Удалён старый site-бэкап: $(basename "${archives[i]}")"
    rm -f "${archives[i]}"
  done
}

backup_print_list() {
  local dir="${1:-${WEB_CHAT_DB_BACKUP_DIR}}"
  local n=0
  local path
  echo "Каталог: ${dir}"
  echo ""
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    n=$((n + 1))
    local base size
    base="$(basename "$path")"
    size="$(wc -c <"$path" 2>/dev/null | tr -d ' ')"
    printf '  %2d  %s  (%s bytes)\n' "$n" "$base" "$size"
  done < <(backup_list_archives "${dir}")
  if [[ "$n" -eq 0 ]]; then
    echo "  (архивов нет)"
  fi
}
