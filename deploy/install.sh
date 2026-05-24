#!/usr/bin/env bash
#
# Установка и настройка web-chat: venv, .env, каталоги данных, systemd (автозапуск).
#
# Использование:
#   sudo ./deploy/install.sh                    # полная установка (нужен root для systemd)
#   ./deploy/install.sh --skip-systemd          # только venv + .env + каталоги
#   ./deploy/install.sh --install-root /opt/web-chat --user www-data
#   ./deploy/install.sh --uninstall             # отключить и удалить unit-файлы
#
# Переменные окружения (опционально):
#   WEB_CHAT_INSTALL_ROOT   — каталог проекта (по умолчанию: родитель deploy/)
#   WEB_CHAT_RUN_USER       — пользователь systemd (по умолчанию: текущий)
#   WEB_CHAT_RUN_GROUP      — группа systemd
#   WEB_CHAT_WEB_PORT       — HTTP-порт в unit (по умолчанию из .env или 8090)
#   WEB_CHAT_SKIP_TESTS=1   — не запускать pytest после установки
#   WEB_CHAT_DEV_DEPS=1     — pip install -r requirements-dev.txt
#

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="$(cd "${WEB_CHAT_INSTALL_ROOT:-$(dirname "$DEPLOY_DIR")}" && pwd)"
RUN_USER="${WEB_CHAT_RUN_USER:-$(id -un)}"
RUN_GROUP="${WEB_CHAT_RUN_GROUP:-$(id -gn)}"
WEB_PORT="${WEB_CHAT_WEB_PORT:-8090}"
SKIP_SYSTEMD=0
SKIP_TESTS=0
DEV_DEPS=0
DO_UNINSTALL=0
INSTALL_LOGROTATE=0

SERVICE_NAME="web-chat.service"
CLEANUP_SERVICE="web-chat-cleanup.service"
CLEANUP_TIMER="web-chat-cleanup.timer"
LOGROTATE_NAME="web-chat"

info() { printf '%s\n' "[install] $*"; }
warn() { printf '%s\n' "[install] WARN: $*" >&2; }
die() { printf '%s\n' "[install] ERROR: $*" >&2; exit 1; }

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  printf '\nОпции:\n'
  printf '  --install-root PATH   каталог проекта\n'
  printf '  --user USER           пользователь systemd\n'
  printf '  --group GROUP         группа systemd\n'
  printf '  --port PORT           порт HTTP в unit-файле\n'
  printf '  --skip-systemd        не трогать systemd\n'
  printf '  --skip-tests          не запускать pytest\n'
  printf '  --dev-deps            requirements-dev.txt\n'
  printf '  --logrotate           установить /etc/logrotate.d/web-chat\n'
  printf '  --uninstall           удалить unit из systemd\n'
  printf '  -h, --help            эта справка\n'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root)
      INSTALL_ROOT="$(cd "$2" && pwd)"
      shift 2
      ;;
    --user)
      RUN_USER="$2"
      shift 2
      ;;
    --group)
      RUN_GROUP="$2"
      shift 2
      ;;
    --port)
      WEB_PORT="$2"
      shift 2
      ;;
    --skip-systemd)
      SKIP_SYSTEMD=1
      shift
      ;;
    --skip-tests)
      SKIP_TESTS=1
      shift
      ;;
    --dev-deps)
      DEV_DEPS=1
      shift
      ;;
    --logrotate)
      INSTALL_LOGROTATE=1
      shift
      ;;
    --uninstall)
      DO_UNINSTALL=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "неизвестный аргумент: $1 (см. --help)"
      ;;
  esac
done

read_env_var() {
  local key="$1" default="${2:-}"
  local env_file="${INSTALL_ROOT}/.env"
  [[ -f "$env_file" ]] || {
    printf '%s' "$default"
    return
  }
  local line val
  line="$(grep -E "^[[:space:]]*${key}=" "$env_file" 2>/dev/null | tail -1 || true)"
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

render_template() {
  local src="$1" dest="$2"
  sed \
    -e "s|@@INSTALL_ROOT@@|${INSTALL_ROOT}|g" \
    -e "s|@@RUN_USER@@|${RUN_USER}|g" \
    -e "s|@@RUN_GROUP@@|${RUN_GROUP}|g" \
    -e "s|@@WEB_PORT@@|${WEB_PORT}|g" \
    "$src" >"$dest"
}

check_requirements() {
  info "проверка системных требований…"
  local py=""
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      py="$candidate"
      break
    fi
  done
  [[ -n "$py" ]] || die "нужен Python 3.11+ (python3.11 / python3.12)"
  local ver
  ver="$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python $ver найден, требуется >= 3.11"

  command -v curl >/dev/null 2>&1 || warn "curl не найден — проверка health после установки может не сработать"
  command -v sqlite3 >/dev/null 2>&1 || warn "sqlite3 не найден — бэкап legacy SQLite использует копирование файла"

  local db_url
  db_url="$(read_env_var DATABASE_URL "")"
  if [[ "$db_url" == postgresql* ]] || [[ "$db_url" == postgres://* ]]; then
    command -v pg_dump >/dev/null 2>&1 \
      || warn "postgresql-client (pg_dump) не найден — установите для scripts/backup-postgres.sh"
    if command -v systemctl >/dev/null 2>&1; then
      systemctl is-active postgresql >/dev/null 2>&1 \
        || warn "postgresql.service не active — проверьте БД перед запуском web-chat"
    fi
  else
    command -v sqlite3 >/dev/null 2>&1 || true
  fi

  if [[ "$(uname -s)" != "Linux" ]]; then
    warn "скрипт рассчитан на Linux; systemd пропущен на других ОС"
    SKIP_SYSTEMD=1
  fi

  [[ -f "${INSTALL_ROOT}/requirements.txt" ]] || die "не найден ${INSTALL_ROOT}/requirements.txt"
  [[ -f "${INSTALL_ROOT}/app/main.py" ]] || die "не похоже на корень web-chat: ${INSTALL_ROOT}"
  info "Python: $py ($ver)"
}

setup_venv() {
  info "виртуальное окружение: ${INSTALL_ROOT}/.venv"
  local py=""
  for candidate in python3.12 python3.11 python3; do
    command -v "$candidate" >/dev/null 2>&1 && py="$candidate" && break
  done
  [[ -d "${INSTALL_ROOT}/.venv" ]] || "$py" -m venv "${INSTALL_ROOT}/.venv"
  # shellcheck source=/dev/null
  source "${INSTALL_ROOT}/.venv/bin/activate"
  pip install -q --upgrade pip wheel
  if [[ "$DEV_DEPS" == "1" ]]; then
    pip install -q -r "${INSTALL_ROOT}/requirements-dev.txt"
  else
    pip install -q -r "${INSTALL_ROOT}/requirements.txt"
  fi
}

setup_env_file() {
  if [[ ! -f "${INSTALL_ROOT}/.env" ]]; then
    cp "${INSTALL_ROOT}/.env.example" "${INSTALL_ROOT}/.env"
    info "создан ${INSTALL_ROOT}/.env из .env.example — отредактируйте PUBLIC_BASE_URL и URL LLM/SD"
  else
    info ".env уже существует — не перезаписываем"
  fi
  WEB_PORT="$(read_env_var WEB_PORT "$WEB_PORT")"
}

setup_data_dirs() {
  info "каталоги data/ и logs/"
  mkdir -p \
    "${INSTALL_ROOT}/data/db" \
    "${INSTALL_ROOT}/data/uploads" \
    "${INSTALL_ROOT}/data/generated/thumbs" \
    "${INSTALL_ROOT}/logs"
  chmod 750 "${INSTALL_ROOT}/data" 2>/dev/null || true
  mkdir -p "${INSTALL_ROOT}/data/backups/database"
  chmod +x \
    "${INSTALL_ROOT}/scripts/backup-database.sh" \
    "${INSTALL_ROOT}/scripts/restore-database.sh" \
    "${INSTALL_ROOT}/scripts/backup-all.sh" \
    "${INSTALL_ROOT}/scripts/backup-postgres.sh" \
    "${INSTALL_ROOT}/deploy/backup-database.sh" \
    "${INSTALL_ROOT}/deploy/restore-database.sh" \
    "${INSTALL_ROOT}/deploy/backup-data.sh" \
    "${INSTALL_ROOT}/deploy/backup-postgres.sh" \
    2>/dev/null || true
  if [[ "$(id -un)" == "root" && "$RUN_USER" != "root" ]]; then
    chown -R "${RUN_USER}:${RUN_GROUP}" "${INSTALL_ROOT}/data" "${INSTALL_ROOT}/logs"
    info "владелец data/ и logs/: ${RUN_USER}:${RUN_GROUP}"
  fi
}

install_systemd_units() {
  [[ "$SKIP_SYSTEMD" == "1" ]] && {
    info "пропуск systemd (--skip-systemd)"
    return 0
  }
  [[ "$(id -u)" -eq 0 ]] || die "для установки systemd запустите: sudo $0"

  command -v systemctl >/dev/null 2>&1 || die "systemctl не найден"

  local gen_dir="${INSTALL_ROOT}/deploy/generated"
  mkdir -p "$gen_dir"

  render_template \
    "${DEPLOY_DIR}/web-chat.service.template" \
    "${gen_dir}/${SERVICE_NAME}"
  render_template \
    "${DEPLOY_DIR}/web-chat-cleanup.service.template" \
    "${gen_dir}/${CLEANUP_SERVICE}"
  cp "${DEPLOY_DIR}/web-chat-cleanup.timer" "${gen_dir}/${CLEANUP_TIMER}"

  info "копирование unit-файлов в /etc/systemd/system/"
  install -m 0644 "${gen_dir}/${SERVICE_NAME}" "/etc/systemd/system/${SERVICE_NAME}"
  install -m 0644 "${gen_dir}/${CLEANUP_SERVICE}" "/etc/systemd/system/${CLEANUP_SERVICE}"
  install -m 0644 "${gen_dir}/${CLEANUP_TIMER}" "/etc/systemd/system/${CLEANUP_TIMER}"

  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}" "${CLEANUP_TIMER}"
  systemctl restart "${SERVICE_NAME}"
  systemctl restart "${CLEANUP_TIMER}" 2>/dev/null || true

  info "сервис включён в автозагрузку: ${SERVICE_NAME}, ${CLEANUP_TIMER}"
  systemctl --no-pager --full status "${SERVICE_NAME}" | head -15 || true
}

install_logrotate() {
  [[ "$INSTALL_LOGROTATE" == "1" ]] || return 0
  [[ "$(id -u)" -eq 0 ]] || die "для logrotate нужен root"
  local gen="${INSTALL_ROOT}/deploy/generated/logrotate-web-chat.conf"
  render_template "${DEPLOY_DIR}/logrotate-web-chat.conf.template" "$gen"
  install -m 0644 "$gen" "/etc/logrotate.d/${LOGROTATE_NAME}"
  info "logrotate: /etc/logrotate.d/${LOGROTATE_NAME}"
}

run_tests() {
  [[ "$SKIP_TESTS" == "1" ]] && return 0
  info "pytest…"
  # shellcheck source=/dev/null
  source "${INSTALL_ROOT}/.venv/bin/activate"
  (cd "$INSTALL_ROOT" && pytest -q)
}

smoke_health() {
  local base
  base="$(read_env_var PUBLIC_BASE_URL "http://127.0.0.1:${WEB_PORT}")"
  base="${base%/}"
  if curl -sf "${base}/api/health" >/dev/null 2>&1; then
    info "health OK: ${base}/api/health"
    curl -sf "${base}/api/health" 2>/dev/null | head -c 300 || true
    printf '\n'
  else
    warn "health пока недоступен — проверьте .env, LLM/SD и: systemctl status ${SERVICE_NAME}"
  fi
}

uninstall_systemd() {
  [[ "$(id -u)" -eq 0 ]] || die "для --uninstall нужен root"
  command -v systemctl >/dev/null 2>&1 || die "systemctl не найден"
  systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
  systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
  systemctl stop "${CLEANUP_TIMER}" 2>/dev/null || true
  systemctl disable "${CLEANUP_TIMER}" 2>/dev/null || true
  rm -f \
    "/etc/systemd/system/${SERVICE_NAME}" \
    "/etc/systemd/system/${CLEANUP_SERVICE}" \
    "/etc/systemd/system/${CLEANUP_TIMER}"
  systemctl daemon-reload
  info "unit-файлы web-chat удалены из systemd"
}

print_next_steps() {
  local base
  base="$(read_env_var PUBLIC_BASE_URL "http://127.0.0.1:${WEB_PORT}")"
  base="${base%/}"
  cat <<EOF

════════════════════════════════════════════════════════════
  web-chat установлен: ${INSTALL_ROOT}
════════════════════════════════════════════════════════════

  1. Отредактируйте .env (обязательно PUBLIC_BASE_URL, LLM, SD):
     nano ${INSTALL_ROOT}/.env

  2. Управление сервисом:
     sudo systemctl status ${SERVICE_NAME}
     sudo systemctl restart ${SERVICE_NAME}
     ${INSTALL_ROOT}/restart.sh status

  3. UI в браузере:
     ${base}/

  4. Документация:
     ${INSTALL_ROOT}/deploy/DEPLOY.md
     ${INSTALL_ROOT}/HANDBOOK.md

════════════════════════════════════════════════════════════
EOF
}

main() {
  if [[ "$DO_UNINSTALL" == "1" ]]; then
    uninstall_systemd
    exit 0
  fi

  info "корень установки: ${INSTALL_ROOT}"
  info "пользователь сервиса: ${RUN_USER}:${RUN_GROUP}"

  check_requirements
  setup_env_file
  setup_venv
  setup_data_dirs
  install_systemd_units
  install_logrotate
  run_tests
  smoke_health
  print_next_steps
}

main "$@"
