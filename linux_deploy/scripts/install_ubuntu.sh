#!/bin/bash
# ============================================================
# Ubuntu one-shot deployment script
# Usage: sudo bash install_ubuntu.sh
# Prereq: code synced to /opt/effic, migration_pack at /tmp/migration_pack
# ============================================================
set -euo pipefail

APP_DIR="/opt/effic"
APP_USER="www-data"
MIGRATION_DIR="${MIGRATION_DIR:-/tmp/migration_pack}"
DB_NAME="device_inventory_system"
DB_USER="effic"
DB_PASSWORD="${DB_PASSWORD:-}"   # 数据库用户密码，必须通过环境变量 DB_PASSWORD 或手动修改提供

if [ -z "$DB_PASSWORD" ]; then
    echo "[ERROR] 数据库密码未设置。请执行: DB_PASSWORD='your_strong_password' sudo bash install_ubuntu.sh"
    echo "        或直接编辑脚本顶部 DB_PASSWORD 变量。"
    exit 1
fi

echo "============================================"
echo "  effic Ubuntu deployment"
echo "============================================"

# ---- 1. System packages ----
echo "[1/8] Installing system packages ..."
apt-get update
apt-get install -y \
    python3 python3-venv python3-pip python3-dev \
    mysql-server mysql-client libmysqlclient-dev \
    nginx \
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    libgl1 libglib2.0-0 \
    pkg-config

# ---- 2. App directories ----
echo "[2/8] Creating app directory ${APP_DIR} ..."
mkdir -p "${APP_DIR}/logs"
mkdir -p "${APP_DIR}/kdd/uploads"
mkdir -p "${APP_DIR}/kdd/exports"
mkdir -p "${APP_DIR}/static/uploads"

# ---- 3. Python venv ----
echo "[3/8] Creating Python virtualenv ..."
if [ ! -d "${APP_DIR}/venv" ]; then
    python3 -m venv "${APP_DIR}/venv"
fi

# Some cloud hosts (e.g. Huawei Cloud ECS) cannot fetch the pypi.org simple
# index: HEAD returns 200 but the index body is empty, so pip reports
# "from versions: none" for every package. Force a reachable mirror.
PIP_INDEX="${PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PIP_HOST="${PIP_HOST:-pypi.tuna.tsinghua.edu.cn}"
cat > /etc/pip.conf <<EOF
[global]
index-url = ${PIP_INDEX}
trusted-host = ${PIP_HOST}
timeout = 60
EOF
echo "      pip mirror: ${PIP_INDEX}"

"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/linux_deploy/config/requirements.txt"

# ---- 4. MySQL database and user ----
echo "[4/8] Initializing MySQL database and user ..."
mysql -uroot <<EOF
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
EOF

# ---- 5. Import data (if migration_pack exists) ----
if [ -f "${MIGRATION_DIR}/mysql_backup.sql" ]; then
    echo "[5/8] Found ${MIGRATION_DIR}/mysql_backup.sql, importing ..."
    mysql -u"${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}" < "${MIGRATION_DIR}/mysql_backup.sql"

    if [ -f "${MIGRATION_DIR}/express.db" ]; then
        echo "      Copying express.db ..."
        cp "${MIGRATION_DIR}/express.db" "${APP_DIR}/kdd/express.db"
    fi
    if [ -d "${MIGRATION_DIR}/uploads" ]; then
        cp -r "${MIGRATION_DIR}/uploads/." "${APP_DIR}/static/uploads/" || true
    fi
    if [ -d "${MIGRATION_DIR}/kdd_uploads" ]; then
        cp -r "${MIGRATION_DIR}/kdd_uploads/." "${APP_DIR}/kdd/uploads/" || true
    fi
    if [ -d "${MIGRATION_DIR}/kdd_exports" ]; then
        cp -r "${MIGRATION_DIR}/kdd_exports/." "${APP_DIR}/kdd/exports/" || true
    fi
elif [ -f "${MIGRATION_DIR}/data/mysql_backup.sql" ]; then
    echo "[5/8] Found ${MIGRATION_DIR}/data/mysql_backup.sql (package_all layout), importing ..."
    mysql -u"${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}" < "${MIGRATION_DIR}/data/mysql_backup.sql"

    if [ -f "${MIGRATION_DIR}/data/express.db" ]; then
        cp "${MIGRATION_DIR}/data/express.db" "${APP_DIR}/kdd/express.db"
    fi
    if [ -d "${MIGRATION_DIR}/data/uploads" ]; then
        cp -r "${MIGRATION_DIR}/data/uploads/." "${APP_DIR}/static/uploads/" || true
    fi
    if [ -d "${MIGRATION_DIR}/data/kdd_uploads" ]; then
        cp -r "${MIGRATION_DIR}/data/kdd_uploads/." "${APP_DIR}/kdd/uploads/" || true
    fi
    if [ -d "${MIGRATION_DIR}/data/kdd_exports" ]; then
        cp -r "${MIGRATION_DIR}/data/kdd_exports/." "${APP_DIR}/kdd/exports/" || true
    fi
else
    echo "[5/8] No mysql_backup.sql found in ${MIGRATION_DIR}, skipping data import"
fi

# ---- 6. Generate .env ----
echo "[6/8] Generating .env ..."
if [ ! -f "${APP_DIR}/.env" ]; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    JWT=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -e "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" \
        -e "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=${JWT}|" \
        "${APP_DIR}/linux_deploy/config/.env.example" > "${APP_DIR}/.env"
    echo "      .env generated with random SECRET_KEY and JWT_SECRET_KEY"
else
    echo "      .env already exists, skipping"
fi

# ITS_SECRET_KEY decrypts companies.its_password stored as "enc:<token>".
# A randomly generated key CANNOT decrypt data migrated from the old Windows
# host: the original key from that host's web.config must be reused.
if ! grep -q '^ITS_SECRET_KEY=.\+' "${APP_DIR}/.env"; then
    echo "      [WARN] ITS_SECRET_KEY not set in ${APP_DIR}/.env."
    echo "             If companies.its_password contains 'enc:' values migrated"
    echo "             from Windows, reuse the ORIGINAL key from web.config, or"
    echo "             APP will report: ITS password decrypt failed."
fi

# ---- 7. Permissions ----
echo "[7/8] Setting file permissions ..."
chown -R ${APP_USER}:${APP_USER} "${APP_DIR}"
chmod 600 "${APP_DIR}/.env"

# ---- 8. systemd + nginx ----
echo "[8/8] Installing systemd unit and nginx site ..."
cp "${APP_DIR}/linux_deploy/systemd/effic.service" /etc/systemd/system/effic.service
systemctl daemon-reload
systemctl enable effic.service

cp "${APP_DIR}/linux_deploy/nginx/effic.conf" /etc/nginx/sites-available/effic
ln -sf /etc/nginx/sites-available/effic /etc/nginx/sites-enabled/effic
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

systemctl restart effic.service

echo ""
echo "============================================"
echo "  Deployment finished"
echo "============================================"
echo "  Service status : systemctl status effic"
echo "  App log        : journalctl -u effic -f"
echo "  Gunicorn log   : tail -f ${APP_DIR}/logs/gunicorn_error.log"
echo "  Health check   : curl http://127.0.0.1/healthz"
echo "  Entry point    : http://<server-ip>/   (Nginx -> Gunicorn:5050)"
echo ""
echo "  Android APP:"
echo "    Point the APP server address to http://<server-ip>/"
echo "    Nginx will forward port 80 to Gunicorn 5050."
echo "    Or use http://<server-ip>:5050 to hit Gunicorn directly"
echo "    (remember to open port 5050 in ufw if so)."
