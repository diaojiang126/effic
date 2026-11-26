#!/bin/bash
# ============================================================
# Windows -> Linux code sync (run on Linux, pulls from Windows)
# Usage: bash sync_code.sh <windows_host> <windows_user> [win_path]
# Note: syncs code only, leaves .env / venv / logs / uploads untouched
# ============================================================
set -euo pipefail

WIN_HOST="${1:?Usage: $0 <windows_host> <windows_user> [win_path]}"
WIN_USER="${2:?Usage: $0 <windows_host> <windows_user> [win_path]}"
WIN_PATH="${3:-C:/luosen/effic}"
APP_DIR="/opt/effic"

echo "Syncing code from ${WIN_USER}@${WIN_HOST}:${WIN_PATH} -> ${APP_DIR}"

rsync -avz --delete \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='logs/' \
    --exclude='static/uploads/' \
    --exclude='kdd/uploads/' \
    --exclude='kdd/exports/' \
    --exclude='kdd/express.db' \
    --exclude='cookies.txt' \
    --exclude='*.bat' \
    --exclude='web.config' \
    --exclude='deploy_*.ps1' \
    --exclude='restart_*.bat' \
    "${WIN_USER}@${WIN_HOST}:${WIN_PATH}/" "${APP_DIR}/"

chown -R www-data:www-data "${APP_DIR}"
echo "Sync done. Restart with: systemctl restart effic"
