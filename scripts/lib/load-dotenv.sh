# shellcheck shell=bash
# Загрузка WEB_CHAT_ROOT и DATABASE_URL из .env (без перезаписи уже заданных переменных).

: "${WEB_CHAT_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

_load_env_key() {
  local key="$1"
  local env_file="${WEB_CHAT_ROOT}/.env"
  [[ -f "$env_file" ]] || return 1
  local line val
  line="$(grep -E "^[[:space:]]*${key}=" "$env_file" 2>/dev/null | tail -1 || true)"
  [[ -n "$line" ]] || return 1
  val="${line#*=}"
  val="${val//$'\r'/}"
  val="${val#\"}"
  val="${val%\"}"
  val="${val#\'}"
  val="${val%\'}"
  printf '%s' "$val"
}

if [[ -z "${DATABASE_URL:-}" ]]; then
  DATABASE_URL="$(_load_env_key DATABASE_URL || true)"
fi

_db_backend() {
  case "${DATABASE_URL:-}" in
    postgresql*|postgres://*) printf 'postgresql' ;;
    sqlite*) printf 'sqlite' ;;
    *) printf 'unknown' ;;
  esac
}

WEB_CHAT_DB_BACKEND="$(_db_backend)"
