#!/usr/bin/env bash
#
# Перезапуск web-chat: systemd (production) или uvicorn вручную (dev).
#
# Использование:
#   ./restart.sh              # перезапуск
#   ./restart.sh status       # health, порты, systemd
#   ./restart.sh dev          # принудительно uvicorn без systemd
#
# Переменные (опционально, иначе читаются из .env):
#   WEB_CHAT_BASE_URL       — для health и DELETE /api/logs
#   WEB_CHAT_PORT           — порт HTTP (WEB_PORT в .env)
#   WEB_CHAT_SERVICE        — web-chat.service
#   WEB_CHAT_CLEANUP_TIMER  — web-chat-cleanup.timer
#   SKIP_API_LOG_CLEAR=1    — не очищать буфер журнала API
#   SKIP_SYSTEMD=1          — не использовать systemctl
#   CLEAR_FILES_DIR         — каталог *.log (по умолчанию $ROOT/logs)
#   VACUUM_SYSTEMD_JOURNAL=1 — journalctl --vacuum-time
#   JOURNAL_VACUUM_TIME     — по умолчанию 7d
#
# Hook: исполняемый restart-hook.sh в корне проекта вызывается в конце.
#

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WEB_CHAT_SERVICE="${WEB_CHAT_SERVICE:-web-chat.service}"
WEB_CHAT_CLEANUP_TIMER="${WEB_CHAT_CLEANUP_TIMER:-web-chat-cleanup.timer}"
CLEAR_FILES_DIR="${CLEAR_FILES_DIR:-$ROOT/logs}"
JOURNAL_VACUUM_TIME="${JOURNAL_VACUUM_TIME:-7d}"

info() { printf '%s\n' "[restart] $*"; }
warn() { printf '%s\n' "[restart] WARN: $*" >&2; }

# Прочитать одну переменную из .env (без eval всего файла).
read_env_var() {
  local key="$1" default="${2:-}"
  [[ -f "$ROOT/.env" ]] || {
    printf '%s' "$default"
    return
  }
  local line val
  line="$(grep -E "^[[:space:]]*${key}=" "$ROOT/.env" 2>/dev/null | tail -1 || true)"
  [[ -n "$line" ]] || {
    printf '%s' "$default"
    return
  }
  val="${line#*=}"
  val="${val//$'\r'/}"
  val="${val#\"}"
  val="${val%\"}"
  val="${val#\'}"
  val="${val%\'}"
  [[ -n "$val" ]] && printf '%s' "$val" || printf '%s' "$default"
}

WEB_CHAT_PORT="${WEB_CHAT_PORT:-$(read_env_var WEB_PORT 8090)}"
_DEFAULT_BASE="http://127.0.0.1:${WEB_CHAT_PORT}"
WEB_CHAT_BASE_URL="${WEB_CHAT_BASE_URL:-$(read_env_var PUBLIC_BASE_URL "$_DEFAULT_BASE")}"
MCP_PORT_HINT="$(read_env_var MCP_PORT 0)"
if [[ "$MCP_PORT_HINT" == "0" || -z "$MCP_PORT_HINT" ]]; then
  MCP_PORT_HINT=$((WEB_CHAT_PORT + 1))
fi

run_hook() {
  local hook="$ROOT/restart-hook.sh"
  if [[ -x "$hook" ]]; then
    info "запуск hook: $hook"
    "$hook"
  elif [[ -f "$hook" ]]; then
    warn "$hook существует, но не исполняемый — chmod +x $hook"
  fi
}

clear_api_log_buffer() {
  if [[ "${SKIP_API_LOG_CLEAR:-0}" == "1" ]]; then
    info "пропуск DELETE /api/logs (SKIP_API_LOG_CLEAR=1)"
    return 0
  fi
  local url="${WEB_CHAT_BASE_URL%/}/api/logs"
  if curl -sf -o /dev/null -X DELETE "$url"; then
    info "очищен кольцевой буфер журнала (DELETE $url)"
  else
    warn "не удалось вызвать DELETE $url (сервис выключен или недоступен)"
  fi
}

clear_local_log_files() {
  [[ -d "$CLEAR_FILES_DIR" ]] || return 0
  local -a files=()
  shopt -s nullglob
  files=( "$CLEAR_FILES_DIR"/*.log "$CLEAR_FILES_DIR"/*.log.* )
  shopt -u nullglob
  [[ "${#files[@]}" -eq 0 ]] && return 0
  rm -f -- "${files[@]}"
  info "удалены локальные журналы в $CLEAR_FILES_DIR (${#files[@]} шт.)"
}

vacuum_journal_if_requested() {
  if [[ "${VACUUM_SYSTEMD_JOURNAL:-0}" != "1" ]]; then
    return 0
  fi
  if ! command -v journalctl >/dev/null 2>&1; then
    warn "journalctl не найден"
    return 0
  fi
  info "journalctl --vacuum-time=$JOURNAL_VACUUM_TIME"
  journalctl --vacuum-time="$JOURNAL_VACUUM_TIME"
}

restart_uvicorn_dev() {
  local venv_uvicorn="$ROOT/.venv/bin/uvicorn"
  local log_file="$ROOT/logs/uvicorn.log"
  local port="$WEB_CHAT_PORT"

  if [[ ! -x "$venv_uvicorn" ]]; then
    warn "не найден $venv_uvicorn — выполните: python3 -m venv .venv && pip install -r requirements-dev.txt"
    return 1
  fi

  mkdir -p "$ROOT/logs"

  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" 2>/dev/null || true
  elif command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(lsof -ti ":$port" 2>/dev/null || true)"
    [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
  else
    pkill -f "uvicorn app.main:app.*--port $port" 2>/dev/null || true
  fi
  sleep 1

  info "uvicorn :$port (лог: $log_file); MCP ожидается на :$MCP_PORT_HINT"
  nohup "$venv_uvicorn" app.main:app --host 0.0.0.0 --port "$port" >>"$log_file" 2>&1 &
  sleep 2

  if curl -sf "${WEB_CHAT_BASE_URL%/}/api/health" >/dev/null 2>&1; then
    info "health OK — $WEB_CHAT_BASE_URL/api/health"
    curl -sf "${WEB_CHAT_BASE_URL%/}/api/health" 2>/dev/null | head -c 200 || true
    printf '\n'
  else
    warn "health не отвечает — см. $log_file"
    return 1
  fi
}

restart_systemd_units() {
  if [[ "${SKIP_SYSTEMD:-0}" == "1" ]]; then
    restart_uvicorn_dev
    return $?
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl недоступен — режим dev (uvicorn)"
    restart_uvicorn_dev
    return $?
  fi

  systemctl daemon-reload 2>/dev/null || true

  if systemctl cat "$WEB_CHAT_SERVICE" &>/dev/null; then
    info "systemctl restart $WEB_CHAT_SERVICE"
    systemctl restart "$WEB_CHAT_SERVICE"
    sleep 2
    if curl -sf "${WEB_CHAT_BASE_URL%/}/api/health" >/dev/null 2>&1; then
      info "health OK"
    else
      warn "после restart health не отвечает на $WEB_CHAT_BASE_URL"
    fi
    systemctl --no-pager --full status "$WEB_CHAT_SERVICE" 2>/dev/null | head -20 || true
  else
    warn "юнит $WEB_CHAT_SERVICE не установлен — режим dev"
    warn "установка: см. deploy/DEPLOY.md"
    restart_uvicorn_dev
    return $?
  fi

  if systemctl cat "$WEB_CHAT_CLEANUP_TIMER" &>/dev/null; then
    info "systemctl restart $WEB_CHAT_CLEANUP_TIMER"
    systemctl restart "$WEB_CHAT_CLEANUP_TIMER" 2>/dev/null || true
  fi
}

cmd_status() {
  info "корень: $ROOT"
  info "HTTP :$WEB_CHAT_PORT | MCP :$MCP_PORT_HINT | PUBLIC_BASE_URL=$WEB_CHAT_BASE_URL"

  if command -v systemctl >/dev/null 2>&1 && systemctl cat "$WEB_CHAT_SERVICE" &>/dev/null; then
    systemctl is-active "$WEB_CHAT_SERVICE" 2>/dev/null || true
    systemctl is-enabled "$WEB_CHAT_SERVICE" 2>/dev/null || true
  fi

  if curl -sf "${WEB_CHAT_BASE_URL%/}/api/health" 2>/dev/null; then
    printf '\n'
  else
    warn "GET /api/health недоступен"
  fi

  if [[ -d "$CLEAR_FILES_DIR" ]]; then
    local count
    count="$(find "$CLEAR_FILES_DIR" -maxdepth 1 -name '*.log*' 2>/dev/null | wc -l)"
    info "файлов журнала в $CLEAR_FILES_DIR: $count"
  fi
}

cmd_restart() {
  info "корень проекта: $ROOT"
  clear_api_log_buffer
  clear_local_log_files
  vacuum_journal_if_requested
  restart_systemd_units
  run_hook
  info "готово. UI: ${WEB_CHAT_BASE_URL%/}/  галерея: ${WEB_CHAT_BASE_URL%/}/gallery"
}

main() {
  local cmd="${1:-restart}"
  case "$cmd" in
    status)
      cmd_status
      ;;
    dev)
      SKIP_SYSTEMD=1
      cmd_restart
      ;;
    restart | "")
      cmd_restart
      ;;
    -h | --help)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
      ;;
    *)
      warn "неизвестная команда: $cmd (используйте: restart | status | dev | --help)"
      exit 1
      ;;
  esac
}

main "$@"
