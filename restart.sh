#!/usr/bin/env bash
#
# Перезапуск web-chat после критичных изменений + очистка логов (насколько возможно).
#
# Использование:
#   ./restart.sh
#   WEB_CHAT_BASE_URL=http://127.0.0.1:8090 ./restart.sh
#
# Переменные окружения (опционально):
#   WEB_CHAT_BASE_URL     — базовый URL для DELETE /api/logs до рестарта (по умолчанию http://127.0.0.1:8090)
#   WEB_CHAT_SERVICE      — юнит приложения (по умолчанию web-chat.service)
#   WEB_CHAT_CLEANUP_TIMER — таймер retention (по умолчанию web-chat-cleanup.timer)
#   SKIP_API_LOG_CLEAR    — если 1, не вызывать DELETE /api/logs
#   SKIP_SYSTEMD          — если 1, не трогать systemd
#   CLEAR_FILES_DIR       — каталог с файлами *.log для удаления (по умолчанию $ROOT/logs, если есть)
#   VACUUM_SYSTEMD_JOURNAL — если 1, выполнить journalctl --vacuum-time (см. JOURNAL_VACUUM_TIME)
#   JOURNAL_VACUUM_TIME   — аргумент для journalctl --vacuum-time (например 7d или 1h); по умолчанию 7d
#
# Будущее: положите исполняемый файл restart-hook.sh рядом со скриптом — он будет вызван в конце.
#

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WEB_CHAT_BASE_URL="${WEB_CHAT_BASE_URL:-http://127.0.0.1:8090}"
WEB_CHAT_SERVICE="${WEB_CHAT_SERVICE:-web-chat.service}"
WEB_CHAT_CLEANUP_TIMER="${WEB_CHAT_CLEANUP_TIMER:-web-chat-cleanup.timer}"
CLEAR_FILES_DIR="${CLEAR_FILES_DIR:-$ROOT/logs}"
JOURNAL_VACUUM_TIME="${JOURNAL_VACUUM_TIME:-7d}"

info() { printf '%s\n' "[restart] $*"; }
warn() { printf '%s\n' "[restart] WARN: $*" >&2; }

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
    warn "не удалось вызвать DELETE $url (сервис выключен или недоступен — после рестарта буфер будет пустым)"
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
  info "удалены локальные файлы журналов в $CLEAR_FILES_DIR (${#files[@]} шт.)"
}

vacuum_journal_if_requested() {
  if [[ "${VACUUM_SYSTEMD_JOURNAL:-0}" != "1" ]]; then
    return 0
  fi
  if ! command -v journalctl >/dev/null 2>&1; then
    warn "journalctl не найден — пропуск очистки journal"
    return 0
  fi
  info "journalctl --vacuum-time=$JOURNAL_VACUUM_TIME (глобально для journald)"
  journalctl --vacuum-time="$JOURNAL_VACUUM_TIME"
}

restart_uvicorn_dev() {
  local venv_uvicorn="$ROOT/.venv/bin/uvicorn"
  local log_file="$ROOT/logs/uvicorn.log"
  local port="${WEB_CHAT_PORT:-8090}"

  if [[ ! -x "$venv_uvicorn" ]]; then
    warn "не найден $venv_uvicorn — создайте venv и установите зависимости"
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

  info "запуск uvicorn на порту $port (лог: $log_file)"
  nohup "$venv_uvicorn" app.main:app --host 0.0.0.0 --port "$port" >>"$log_file" 2>&1 &
  sleep 2

  if curl -sf "${WEB_CHAT_BASE_URL%/}/api/health" >/dev/null 2>&1 \
    || curl -sf "${WEB_CHAT_BASE_URL%/}/api/config" >/dev/null 2>&1; then
    info "сервер отвечает на $WEB_CHAT_BASE_URL"
  else
    warn "сервер ещё не отвечает — проверьте $log_file"
    return 1
  fi
}

restart_systemd_units() {
  if [[ "${SKIP_SYSTEMD:-0}" == "1" ]]; then
    warn "systemd пропущен (SKIP_SYSTEMD=1) — перезапуск uvicorn вручную"
    restart_uvicorn_dev
    return $?
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl недоступен — перезапуск uvicorn вручную"
    restart_uvicorn_dev
    return $?
  fi

  systemctl daemon-reload 2>/dev/null || true

  if systemctl cat "$WEB_CHAT_SERVICE" &>/dev/null; then
    info "systemctl restart $WEB_CHAT_SERVICE"
    systemctl restart "$WEB_CHAT_SERVICE"
    systemctl --no-pager --full status "$WEB_CHAT_SERVICE" || true
  else
    warn "юнит $WEB_CHAT_SERVICE не найден"
    restart_uvicorn_dev
    return $?
  fi

  if systemctl cat "$WEB_CHAT_CLEANUP_TIMER" &>/dev/null; then
    info "systemctl restart $WEB_CHAT_CLEANUP_TIMER"
    systemctl restart "$WEB_CHAT_CLEANUP_TIMER" || true
  fi
}

main() {
  info "корень проекта: $ROOT"

  clear_api_log_buffer
  clear_local_log_files
  vacuum_journal_if_requested
  restart_systemd_units

  run_hook

  info "готово."
}

main "$@"
