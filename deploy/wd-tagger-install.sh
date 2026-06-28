#!/usr/bin/env bash
#
# Установка WD14 tagger для web-chat (ONNX worker + venv).
#
# Использование:
#   sudo ./deploy/wd-tagger-install.sh
#   ./deploy/wd-tagger-install.sh --skip-warmup
#
# Переменные (опционально):
#   WD_TAGGER_ROOT=/opt/wd-tagger
#   WD14_STANDALONE_ROOT=/opt/wd14-tagger-standalone
#   WD_TAGGER_MODEL=wd14-vit.v2
#

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"

WD_TAGGER_ROOT="${WD_TAGGER_ROOT:-/opt/wd-tagger}"
WD14_STANDALONE_ROOT="${WD14_STANDALONE_ROOT:-/opt/wd14-tagger-standalone}"
WD_TAGGER_VENV="${WD_TAGGER_ROOT}/venv"
WD_TAGGER_PYTHON="${WD_TAGGER_VENV}/bin/python"
WD_TAGGER_HF_HOME="${WD_TAGGER_ROOT}/hf_cache"
WD_TAGGER_MODEL="${WD_TAGGER_MODEL:-wd14-vit.v2}"
WD_TAGGER_REPO="${WD_TAGGER_REPO:-https://github.com/corkborg/wd14-tagger-standalone.git}"
SKIP_WARMUP=0

info() { printf '%s\n' "[wd-tagger] $*"; }
warn() { printf '%s\n' "[wd-tagger] WARN: $*" >&2; }
die() { printf '%s\n' "[wd-tagger] ERROR: $*" >&2; exit 1; }

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  printf '\nОпции:\n'
  printf '  --skip-warmup   не скачивать модель HF при установке\n'
  printf '  -h, --help      эта справка\n'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-warmup)
      SKIP_WARMUP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "неизвестный аргумент: $1 (см. --help)"
      ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  die "нужен git"
fi
if ! command -v python3 >/dev/null 2>&1; then
  die "нужен python3"
fi

info "WD14 standalone → ${WD14_STANDALONE_ROOT}"
if [[ -d "${WD14_STANDALONE_ROOT}/.git" ]]; then
  info "репозиторий уже есть, git pull"
  git -C "${WD14_STANDALONE_ROOT}" pull --ff-only
elif [[ -d "${WD14_STANDALONE_ROOT}" ]]; then
  die "${WD14_STANDALONE_ROOT} существует, но это не git clone"
else
  sudo mkdir -p "$(dirname "${WD14_STANDALONE_ROOT}")"
  sudo git clone "${WD_TAGGER_REPO}" "${WD14_STANDALONE_ROOT}"
fi

if [[ ! -f "${WD14_STANDALONE_ROOT}/run.py" ]]; then
  die "не найден ${WD14_STANDALONE_ROOT}/run.py"
fi

info "venv → ${WD_TAGGER_VENV}"
sudo mkdir -p "${WD_TAGGER_ROOT}" "${WD_TAGGER_HF_HOME}"
if [[ ! -x "${WD_TAGGER_PYTHON}" ]]; then
  sudo python3 -m venv "${WD_TAGGER_VENV}"
fi

info "pip install (standalone + Pillow)"
sudo "${WD_TAGGER_PYTHON}" -m pip install --upgrade pip wheel
sudo "${WD_TAGGER_PYTHON}" -m pip install -r "${WD14_STANDALONE_ROOT}/requirements.txt"
# opencv-python тянет libGL; на headless-хосте — headless-сборка
sudo "${WD_TAGGER_PYTHON}" -m pip uninstall -y opencv-python 2>/dev/null || true
sudo "${WD_TAGGER_PYTHON}" -m pip install "opencv-python-headless==4.11.0.86"

if command -v nvidia-smi >/dev/null 2>&1; then
  info "CUDA обнаружена — onnxruntime-gpu"
  if ! sudo "${WD_TAGGER_PYTHON}" -m pip install onnxruntime-gpu 2>/dev/null; then
    warn "onnxruntime-gpu не установился, остаёмся на CPU onnxruntime"
    sudo "${WD_TAGGER_PYTHON}" -m pip install onnxruntime
  fi
else
  info "CUDA не найдена — onnxruntime (CPU)"
  sudo "${WD_TAGGER_PYTHON}" -m pip install onnxruntime
fi

if [[ "${SKIP_WARMUP}" -eq 0 ]]; then
  info "прогрев модели ${WD_TAGGER_MODEL} (HF cache: ${WD_TAGGER_HF_HOME})"
  TEST_PNG="$(mktemp /tmp/wd-tagger-warmup.XXXXXX.png)"
  printf '%s' 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==' \
    | base64 -d >"${TEST_PNG}"
  export HF_HOME="${WD_TAGGER_HF_HOME}"
  export HF_HUB_CACHE="${WD_TAGGER_HF_HOME}/hub"
  export HF_HUB_DISABLE_SYMLINKS_WARNING=1
  sudo -E env HF_HOME="${HF_HOME}" HF_HUB_CACHE="${HF_HUB_CACHE}" \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    "${WD_TAGGER_PYTHON}" "${WD14_STANDALONE_ROOT}/run.py" \
    --model "${WD_TAGGER_MODEL}" --file "${TEST_PNG}" >/dev/null
  rm -f "${TEST_PNG}"
  info "модель загружена"
else
  info "прогрев пропущен (--skip-warmup)"
fi

info "проверка IPC worker"
WORKER_SCRIPT="${PROJECT_ROOT}/app/scripts/wd_tagger_worker.py"
if [[ ! -f "${WORKER_SCRIPT}" ]]; then
  die "не найден ${WORKER_SCRIPT}"
fi
PING_OUT="$(
  export HF_HOME="${WD_TAGGER_HF_HOME}"
  export HF_HUB_CACHE="${WD_TAGGER_HF_HOME}/hub"
  export HF_HUB_DISABLE_SYMLINKS_WARNING=1
  printf '%s\n' '{"cmd":"ping"}' | sudo -E env HF_HOME="${HF_HOME}" HF_HUB_CACHE="${HF_HUB_CACHE}" \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    timeout 120 "${WD_TAGGER_PYTHON}" "${WORKER_SCRIPT}" \
    --run-py "${WD14_STANDALONE_ROOT}/run.py" \
    --model "${WD_TAGGER_MODEL}" \
    --threshold 0.35
)"
if ! grep -q '"ok": true' <<<"${PING_OUT}"; then
  die "worker ping failed: ${PING_OUT}"
fi
printf '%s\n' '{"cmd":"shutdown"}' | sudo -E env HF_HOME="${HF_HOME}" HF_HUB_CACHE="${HF_HUB_CACHE}" \
  HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
  timeout 30 "${WD_TAGGER_PYTHON}" "${WORKER_SCRIPT}" \
  --run-py "${WD14_STANDALONE_ROOT}/run.py" \
  --model "${WD_TAGGER_MODEL}" \
  --threshold 0.35 >/dev/null 2>&1 || true

info "готово"
info "  WD_TAGGER_PYTHON=${WD_TAGGER_PYTHON}"
info "  WD_TAGGER_RUN_PY=${WD14_STANDALONE_ROOT}/run.py"
info "  WD_TAGGER_HF_HOME=${WD_TAGGER_HF_HOME}"
info "  WD_TAGGER_MODEL=${WD_TAGGER_MODEL}"
info "перезапустите web-chat: cd ${PROJECT_ROOT} && ./restart.sh"
