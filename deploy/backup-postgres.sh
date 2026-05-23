#!/usr/bin/env bash
# Алиас production → backup-database.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT}/deploy/backup-database.sh" "$@"
