#!/usr/bin/env bash
#
# Перезапуск web-chat и связанных systemd-юнитов (production) или uvicorn (dev).
#
# Использование:
#   ./restart.sh              # перезапуск рабочих сервисов
#   ./restart.sh status       # health, systemd, порты
#   ./restart.sh dev          # uvicorn без systemd (убивает процесс на WEB_PORT)
#   ./restart.sh --help
#
# Переменные (опционально, иначе из .env):
#   WEB_CHAT_BASE_URL         — health и DELETE /api/logs
#   WEB_CHAT_PORT             — HTTP (WEB_PORT в .env)
#   WEB_CHAT_SERVICE          — web-chat.service
#   WEB_CHAT_CLEANUP_TIMER    — web-chat-cleanup.timer
#   WEB_CHAT_CLEANUP_SERVICE  — web-chat-cleanup.service (разовый запуск: RUN_CLEANUP=1)
#   WEB_CHAT_POSTGRES_UNIT    — postgresql@16-main.service (авто, если не задан)
#   WEB_CHAT_NGINX_UNIT       — nginx.service (только reload, если юнит есть)
#   SKIP_API_LOG_CLEAR=1      — не вызывать DELETE /api/logs
#   SKIP_SYSTEMD=1            — режим dev (uvicorn)
#   SKIP_NGINX_RELOAD=1       — не делать nginx -s reload
#   RUN_CLEANUP=1             — после restart: systemctl start web-chat-cleanup.service
#   CLEAR_FILES_DIR           — $ROOT/logs/*.log
#   VACUUM_SYSTEMD_JOURNAL=1  — journalctl --vacuum-time
#   JOURNAL_VACUUM_TIME       — по умолчанию 7d
#
# Hook: исполняемый restart-hook.sh в корне проекта — в конце restart.
#

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WEB_CHAT_SERVICE="${WEB_CHAT_SERVICE:-web-chat.service}"
WEB_CHAT_CLEANUP_TIMER="${WEB_CHAT_CLEANUP_TIMER:-web-chat-cleanup.timer}"
WEB_CHAT_CLEANUP_SERVICE="${WEB_CHAT_CLEANUP_SERVICE:-web-chat-cleanup.service}"
WEB_CHAT_NGINX_UNIT="${WEB_CHAT_NGINX_UNIT:-nginx.service}"
CLEAR_FILES_DIR="${CLEAR_FILES_DIR:-$ROOT/logs}"
JOURNAL_VACUUM_TIME="${JOURNAL_VACUUM_TIME:-7d}"

info() { printf '%s\n' "[restart] $*"; }
warn() { printf '%s\n' "[restart] WARN: $*" >&2; }

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
DATABASE_URL="$(read_env_var DATABASE_URL "")"
MCP_PORT="$(read_env_var MCP_PORT 0)"
if [[ "$MCP_PORT" == "0" || -z "$MCP_PORT" ]]; then
  MCP_PORT=$((WEB_CHAT_PORT + 1))
fi

uses_postgres() {
  [[ "$DATABASE_URL" == postgresql* ]]
}

systemctl_available() {
  [[ "${SKIP_SYSTEMD:-0}" != "1" ]] && command -v systemctl >/dev/null 2>&1
}

unit_exists() {
  systemctl cat "$1" &>/dev/null
}

discover_postgres_unit() {
  if [[ -n "${WEB_CHAT_POSTGRES_UNIT:-}" ]]; then
    printf '%s' "$WEB_CHAT_POSTGRES_UNIT"
    return
  fi
  local u
  for u in postgresql@16-main.service postgresql@15-main.service postgresql.service; do
    if unit_exists "$u"; then
      printf '%s' "$u"
      return
    fi
  done
}

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
  if curl -sf -o /dev/null -X DELETE "$url" 2>/dev/null; then
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

wait_for_health() {
  local url="${WEB_CHAT_BASE_URL%/}/api/health"
  local max_wait="${WEB_CHAT_HEALTH_WAIT_SEC:-30}"
  local i
  for ((i = 1; i <= max_wait; i++)); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

health_check() {
  local url="${WEB_CHAT_BASE_URL%/}/api/health"
  if curl -sf "$url" >/dev/null 2>&1; then
    info "health OK — $url"
    if command -v jq >/dev/null 2>&1; then
      curl -sf "$url" 2>/dev/null | jq -c \
        '{status, llm, sd, database: .services[]? | select(.id=="database") | .status, mcp: .services[]? | select(.id=="mcp") | .status}' \
        2>/dev/null || curl -sf "$url" 2>/dev/null | head -c 300
      printf '\n'
    else
      curl -sf "$url" 2>/dev/null | head -c 300 || true
      printf '\n'
    fi
    return 0
  fi
  warn "GET $url недоступен"
  return 1
}

print_unit_line() {
  local unit="$1"
  if ! unit_exists "$unit"; then
    info "  $unit — не установлен"
    return
  fi
  local active enabled
  active="$(systemctl is-active "$unit" 2>/dev/null | head -1 | tr -d '[:space:]' || echo unknown)"
  enabled="$(systemctl is-enabled "$unit" 2>/dev/null | head -1 | tr -d '[:space:]' || echo unknown)"
  info "  $unit — active=$active enabled=$enabled"
}

ensure_postgres_running() {
  if ! uses_postgres; then
    info "БД: SQLite (DATABASE_URL не postgresql)"
    return 0
  fi
  local pg_unit
  pg_unit="$(discover_postgres_unit)"
  if [[ -z "$pg_unit" ]]; then
    warn "DATABASE_URL=postgresql*, но unit PostgreSQL не найден"
    return 0
  fi
  if [[ "$(systemctl is-active "$pg_unit" 2>/dev/null || true)" != "active" ]]; then
    info "systemctl start $pg_unit"
    systemctl start "$pg_unit"
    sleep 1
  fi
  print_unit_line "$pg_unit"
}

reload_nginx_if_present() {
  if [[ "${SKIP_NGINX_RELOAD:-0}" == "1" ]]; then
    return 0
  fi
  if ! unit_exists "$WEB_CHAT_NGINX_UNIT"; then
    return 0
  fi
  if nginx -t 2>/dev/null; then
    info "nginx -t OK; systemctl reload $WEB_CHAT_NGINX_UNIT"
    systemctl reload "$WEB_CHAT_NGINX_UNIT" 2>/dev/null || warn "reload $WEB_CHAT_NGINX_UNIT не удался"
  else
    warn "nginx -t failed — reload пропущен"
  fi
}

restart_cleanup_timer() {
  if unit_exists "$WEB_CHAT_CLEANUP_TIMER"; then
    info "systemctl restart $WEB_CHAT_CLEANUP_TIMER"
    systemctl restart "$WEB_CHAT_CLEANUP_TIMER"
    print_unit_line "$WEB_CHAT_CLEANUP_TIMER"
  else
    warn "таймер $WEB_CHAT_CLEANUP_TIMER не установлен (deploy/install.sh)"
  fi
}

run_retention_cleanup_now() {
  if [[ "${RUN_CLEANUP:-0}" != "1" ]]; then
    return 0
  fi
  if unit_exists "$WEB_CHAT_CLEANUP_SERVICE"; then
    info "разовая очистка: systemctl start $WEB_CHAT_CLEANUP_SERVICE"
    systemctl start "$WEB_CHAT_CLEANUP_SERVICE" || warn "cleanup service завершился с ошибкой"
  fi
}

restart_uvicorn_dev() {
  local venv_uvicorn="$ROOT/.venv/bin/uvicorn"
  local log_file="$ROOT/logs/uvicorn.log"
  local port="$WEB_CHAT_PORT"

  if [[ ! -x "$venv_uvicorn" ]]; then
    warn "не найден $venv_uvicorn — python3 -m venv .venv && pip install -r requirements-dev.txt"
    return 1
  fi

  mkdir -p "$ROOT/logs"

  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" 2>/dev/null || true
    fuser -k "${MCP_PORT}/tcp" 2>/dev/null || true
  elif command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(lsof -ti ":$port" 2>/dev/null || true)"
    [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
    pids="$(lsof -ti ":$MCP_PORT" 2>/dev/null || true)"
    [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
  else
    pkill -f "uvicorn app.main:app.*--port $port" 2>/dev/null || true
  fi
  sleep 1

  info "uvicorn :$port (лог: $log_file)"
  info "MCP in-process на :$MCP_PORT (поднимается вместе с app.main)"
  nohup "$venv_uvicorn" app.main:app --host 0.0.0.0 --port "$port" >>"$log_file" 2>&1 &
  info "ожидание health (до ${WEB_CHAT_HEALTH_WAIT_SEC:-30} с)…"
  if ! wait_for_health; then
    warn "см. $log_file и logs/web-chat.log"
    return 1
  fi
  health_check
}

restart_systemd_stack() {
  if ! systemctl_available; then
    warn "systemctl недоступен — режим dev (uvicorn)"
    restart_uvicorn_dev
    return $?
  fi

  systemctl daemon-reload 2>/dev/null || true

  if ! unit_exists "$WEB_CHAT_SERVICE"; then
    warn "юнит $WEB_CHAT_SERVICE не установлен — режим dev"
    warn "установка: sudo ./deploy/install.sh"
    restart_uvicorn_dev
    return $?
  fi

  ensure_postgres_running

  info "systemctl restart $WEB_CHAT_SERVICE"
  systemctl restart "$WEB_CHAT_SERVICE"
  print_unit_line "$WEB_CHAT_SERVICE"

  restart_cleanup_timer
  reload_nginx_if_present
  run_retention_cleanup_now

  info "ожидание health (до ${WEB_CHAT_HEALTH_WAIT_SEC:-30} с)…"
  if ! wait_for_health; then
    warn "после restart health не отвечает — journalctl -u ${WEB_CHAT_SERVICE%.service} -n 80"
    systemctl --no-pager --full status "$WEB_CHAT_SERVICE" 2>/dev/null | head -25 || true
    return 1
  fi
  health_check
}

cmd_status() {
  info "корень: $ROOT"
  info "HTTP :$WEB_CHAT_PORT | MCP (in-process) :$MCP_PORT | PUBLIC_BASE_URL=$WEB_CHAT_BASE_URL"
  if uses_postgres; then
    info "DATABASE_URL — PostgreSQL"
  else
    info "DATABASE_URL — SQLite / прочее"
  fi

  if systemctl_available; then
    info "systemd:"
    print_unit_line "$WEB_CHAT_SERVICE"
    print_unit_line "$WEB_CHAT_CLEANUP_TIMER"
    print_unit_line "$WEB_CHAT_CLEANUP_SERVICE"
    local pg_unit
    pg_unit="$(discover_postgres_unit || true)"
    [[ -n "$pg_unit" ]] && print_unit_line "$pg_unit"
    unit_exists "$WEB_CHAT_NGINX_UNIT" && print_unit_line "$WEB_CHAT_NGINX_UNIT"
  fi

  health_check || true

  if [[ -d "$CLEAR_FILES_DIR" ]]; then
    local count
    count="$(find "$CLEAR_FILES_DIR" -maxdepth 1 -name '*.log*' 2>/dev/null | wc -l)"
    info "файлов журнала в $CLEAR_FILES_DIR: $count"
  fi

  info "внешние зависимости (не перезапускаются этим скриптом): LLM $(read_env_var LLM_BASE_URL —), SD $(read_env_var SD_WEBUI_URL —)"
}

cmd_restart() {
  info "корень проекта: $ROOT"
  clear_api_log_buffer
  clear_local_log_files
  vacuum_journal_if_requested
  restart_systemd_stack
  run_hook
  info "готово. UI: ${WEB_CHAT_BASE_URL%/}/  health: ${WEB_CHAT_BASE_URL%/}/health  галерея: ${WEB_CHAT_BASE_URL%/}/gallery"
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
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      ;;
    *)
      warn "неизвестная команда: $cmd (restart | status | dev | --help)"
      exit 1
      ;;
  esac
}

main "$@"
