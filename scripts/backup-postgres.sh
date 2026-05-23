#!/usr/bin/env bash
# Алиас: бэкап БД (PostgreSQL или SQLite из .env) с ротацией.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT}/scripts/backup-database.sh" "$@"
