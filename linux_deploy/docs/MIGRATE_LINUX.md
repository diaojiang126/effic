# effic 系统 Ubuntu 迁移部署手册

> 将 Flask Web 端从 Windows Server + IIS 迁移到 **Ubuntu + Nginx + Gunicorn**，
> 不影响安卓 APP 端正常使用。本文档基于 **2026-10-23 实测验证**，覆盖首次部署全流程与所有坑位。

---

## 零、先看这个：4 个必踩坑（第一次就翻）

| # | 坑 | 表现 | 正确做法 |
|---|----|------|---------|
| 1 | **Windows 打 zip 带反斜杠路径** | Linux `unzip` 把整个 `code\app.py` 当一个文件名，目录结构全乱 | **不要用** `Compress-Archive`，`package_all.bat` 已改用 `tar.exe`（正斜杠）。解压前先用 `unzip -l 包名.zip \| head -5` 检查分隔符，若看到 `code\app.py`（反斜杠），改用 `7z x` 解压 |
| 2 | **systemd ProtectSystem=strict 把应用目录挂成只读** | Gunicorn worker boot error，Traceback 见 `OSError: [Errno 30] Read-only file system: '...express.db.backup'` | `effic.service` 里的 `ReadWritePaths` 必须放开 **`kdd/` 整个目录**（SQLite 启动时还要在同目录写 `-journal`/`-wal`，只放开单个 `.db` 文件不够） |
| 3 | **依赖清单漏 baidu-aip** | worker boot error，Traceback 见 `ModuleNotFoundError: No module named 'aip'` | `linux_deploy/config/requirements.txt` 已补上 `baidu-aip` 和 `chardet`，首次部署用 `install_ubuntu.sh` 自动装；中途手动补 `sudo /opt/effic/venv/bin/pip install baidu-aip==4.16.13 chardet` |
| 4 | **install_ubuntu.sh 忘了 sudo** | 第一步 apt 就报 `Permission denied`，脚本用了 `set -e` 直接退出 | 整份脚本 8 步全要 root，直接 `sudo bash install_ubuntu.sh` |
| 5 | **云端主机 pip 取不到索引** | `pip install` 报 `ERROR: Could not find a version that satisfies the requirement xxx (from versions: none)`，且任何包都一样，`curl -I https://pypi.org` 却返回 200 | 华为云等环境会拦截/清空 pypi.org 的 simple 索引。`install_ubuntu.sh` 已自动写入 `/etc/pip.conf` 指向清华源；手动补装时加 `-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn` |

---

## 一、迁移前后架构对比

```
Windows（现用）                     Ubuntu（目标）
─────────────────────              ─────────────────────
浏览器 / 安卓APP                    浏览器 / 安卓APP
        │                                  │
        ▼  :80 / :5050                     ▼  :80
┌───────────────────────┐          ┌───────────────────────┐
│ IIS (wfastcgi)        │          │ Nginx (反代 + 静态)   │
│ + Waitress 进程 5050  │          │      │                │
│     └── Flask app.py  │          │      ▼  127.0.0.1:5050│
│ MySQL (Windows)       │          │ Gunicorn (systemd)    │
│ Tesseract (装包)      │          │     └── Flask app.py  │
│ express.db (SQLite)   │          │ MySQL (apt)           │
└───────────────────────┘          │ Tesseract (apt)       │
                                   │ express.db 直接拷贝    │
                                   └───────────────────────┘
```

## 二、目录规划

### Windows 端（仓库内）

```
c:\web\effic\
├── app.py / its_service.py / ...   ← 应用代码不变
├── linux_deploy\                   ← Linux 部署专用
│   ├── config\
│   │   ├── requirements.txt        ← Linux 依赖（已补 baidu-aip）
│   │   └── .env.example
│   ├── systemd\
│   │   └── effic.service           ← 已修正 ReadWritePaths
│   ├── nginx\
│   │   └── effic.conf
│   ├── scripts\
│   │   ├── package_all.bat         ← 打包（tar.exe，正斜杠）
│   │   ├── install_ubuntu.sh       ← 一键部署（sudo 跑）
│   │   ├── sync_code.sh
│   │   └── efficctl.sh             ← restart/log/health/status
│   └── docs\MIGRATE_LINUX.md       ← 本文档
```

### Ubuntu 端（部署后）

```
/opt/effic/                     ← 应用根目录，属主 www-data:www-data
├── app.py / its_service.py / ...
├── venv/
├── .env                        ← 自动生成（随机 SECRET_KEY）
├── logs/
├── static/uploads/
├── kdd/express.db              ← SQLite（express.db.backup 启动时写）
├── kdd/uploads/  kdd/exports/
└── linux_deploy/
```

---

## 三、操作步骤（按顺序执行）

### 步骤 1：Windows 端打包（重装前必做）

在 Windows 旧服务器上（管理员 PowerShell 或 cmd）：

```cmd
cd C:\luosen\effic\linux_deploy\scripts
package_all.bat
```

脚本会自动：
1. 探测 `mysqldump`（不在 PATH 就搜 `C:\Program Files\MySQL\MySQL Server*\bin\`）
2. 导出完整 MySQL 到 `mysql_backup.sql`（utf8mb4 + `--single-transaction`）
3. 复制 `kdd/express.db`、`static/uploads/`、`kdd/uploads/`、`kdd/exports/`
4. 用 **`tar.exe`**（Windows 10+ 自带，写正斜杠）打 zip，排除 `venv/`、`__pycache__/`、`*.bat`、`.ps1`、`副本.env`、`temp_login.json` 等
5. 输出到**当前用户桌面**，例：`C:\Users\Administrator\Desktop\effic_migration_20261023_093327.zip`

**检查打包结果：**

```cmd
dir C:\Users\Administrator\Desktop\effic_migration_*.zip
```

### 步骤 2：传输到 Ubuntu（U 盘 / scp / WinSCP 任选）

```bash
# 方式 A: scp
scp effic_migration_*.zip user@<ubuntu_ip>:/var/backups/effic/

# 方式 B: WinSCP 拖到 /var/backups/effic/

# 方式 C: U 盘挂载
sudo mkdir -p /mnt/usb && sudo mount /dev/sdb1 /mnt/usb
sudo cp /mnt/usb/effic_migration_*.zip /var/backups/effic/
sudo umount /mnt/usb
```

**必放位置：** `/var/backups/effic/`（`/tmp` 重启会清空）。放好后改权限：

```bash
sudo chmod 700 /var/backups/effic
sudo chmod 600 /var/backups/effic/*.zip
```

### 步骤 3：解压并检查 zip 分隔符

```bash
sudo apt install -y unzip p7zip-full
ZIP=/var/backups/effic/effic_migration_*.zip

# 先检查分隔符（关键！）
unzip -l "$ZIP" | head -5
```

**看输出第一列路径：**
- ✅ 正斜杠 `code/app.py` → 直接 `unzip`
- ❌ 反斜杠 `code\app.py` → **必须改 `7z x`**，否则目录结构乱掉

```bash
rm -rf /tmp/migration_pack

# 正斜杠 → unzip
sudo unzip "$ZIP" -d /tmp/migration_pack

# 反斜杠 → 7z（推荐直接用 7z，兼容两种）
sudo 7z x -y -o/tmp/migration_pack "$ZIP" >/dev/null
```

**确认目录结构：**

```bash
ls -lh /tmp/migration_pack
# 期望：code/  data/  PACK_INFO.txt
```

如果多套了一层（比如 `migration_pack/effic_migration_xxx/code`），上提一层：

```bash
cd /tmp/migration_pack && mv effic_migration_*/* . 2>/dev/null; rmdir effic_migration_* 2>/dev/null; ls -lh
```

### 步骤 4：清垃圾文件

zip 里可能混了 Windows 备份文件和敏感副本，部署前清掉：

```bash
cd /tmp/migration_pack/code
rm -f *副本*.env *.env temp_login.json cookies.txt    # 敏感凭据副本
rm -f *.bat *.ps1 web.config deploy_*.ps1 restart_*.bat  # Windows/IIS 专属文件
rm -rf __pycache__ logs android_app patches .trae aspnet_client venv  # 不应打包的
rm -f app - 副本.py kdd/app - 副本.py  # 历史备份
```

### 步骤 5：代码放到 /opt/effic

```bash
sudo mkdir -p /opt/effic
sudo cp -a /tmp/migration_pack/code/. /opt/effic/
sudo chown -R root:root /opt/effic

# 确认关键文件都在根目录（不应出现在 /opt/effic/code/ 里）
ls /opt/effic | grep -E "^(app.py|kdd|templates|static|its_service.py|linux_deploy)$"
```

### 步骤 6：一键部署（必须 sudo）

```bash
cd /opt/effic/linux_deploy/scripts
sudo bash install_ubuntu.sh
```

脚本执行内容（8 步，幂等，可重跑）：

| # | 步骤 | 说明 |
|---|------|------|
| 1 | 装系统包 | `python3-venv python3-pip mysql-server nginx tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng curl vim` |
| 2 | 建目录 | `/opt/effic/logs`、上传目录 |
| 3 | 建 venv + pip install | 依赖来自 `linux_deploy/config/requirements.txt`（已含 baidu-aip） |
| 4 | MySQL | 建库 `device_inventory_system`，建用户 `ls`，授权 `localhost + 127.0.0.1` |
| 5 | 导数据 | 从 `/tmp/migration_pack/data/mysql_backup.sql` 导入；复制 `express.db` 和上传文件 |
| 6 | 生成 .env | 随机 SECRET_KEY / JWT_SECRET_KEY，MySQL 连接串 |
| 7 | chown + systemd + nginx | 属主 www-data；`cp effic.service → /etc/systemd/system/`；`cp effic.conf → /etc/nginx/sites-enabled/` |
| 8 | 启动 + 防火墙 | `systemctl enable --now effic nginx`；`ufw allow 80/tcp` |

**注意：** 不要中途 Ctrl+C，`pip install` 阶段可能慢（尤其编译 opencv）。脚本用了 `set -e`，任何一步失败都会立即退出并打印错误行。

### 步骤 7：必做收尾（install_ubuntu.sh 不包含）

```bash
# 1. 时区（日志时间差 8 小时会让排障全乱）
sudo timedatectl set-timezone Asia/Shanghai && date
# 期望输出: Fri Oct 23 12:xx:xx PM CST 2026

# 2. 让新时区生效
sudo systemctl restart effic
```

**u 的话要先放行 SSH 再启用：**

```bash
sudo ufw allow 22/tcp    # 必须先做，否则掉线
sudo ufw allow 80/tcp
sudo ufw enable
```

### 步骤 8：逐层验证（顺序别乱）

```bash
echo "=== 1. systemd 状态（关键！active 才继续）==="
sudo systemctl status effic --no-pager | head -14
# 期望: Active: active (running) ... Main PID: xxx (gunicorn) ... Tasks: 13 ... Memory: 331.9M
# 不应: auto-restart / code=exited

echo "=== 2. 绕开 systemd 直接跑 Python（最快诊断）==="
cd /opt/effic && sudo -u www-data /opt/effic/venv/bin/python -c "import app" 2>&1 | tail -20
# 期望: 只打印 config 警告 + 数据库初始化信息，无 Traceback

echo "=== 3. Gunicorn 直连 ==="
curl -i http://127.0.0.1:5050/healthz
# 期望: HTTP/1.1 200 OK  +  {"db":true,"ok":true}

echo "=== 4. 经 Nginx 反代 ==="
curl -i http://127.0.0.1/healthz
# 期望: HTTP/1.1 200 OK（不是 502）

echo "=== 5. APP 核心接口 ==="
curl -s http://127.0.0.1/api/public/companies | head -c 300; echo
# 期望: [{"company_code":"711",...},{"company_code":"HQ",...},...]

echo "=== 6. 公网 IP ==="
curl -s http://192.168.170.137/api/public/companies | head -c 200; echo

echo "=== 7. Tesseract ==="
tesseract --list-langs
# 期望: chi_sim  eng
```

## 四、安卓 APP 切换

APP 端**零代码改动**，仅需把服务器地址改到新 Ubuntu IP：

| 旧地址 | 新地址 |
|--------|--------|
| `http://<windows_ip>:5050` | `http://<ubuntu_ip>`（Nginx 80 → Gunicorn 5050） |

**灰度流程：**

1. 一台测试手机改地址 → 登录 → 入库 → 出库 → 库存查询 → 报表导出 → OCR → ITS
2. 全功能正常 → 全量推送

**回滚：** APP 把地址改回 Windows IP 即可（Windows 侧 IIS/Waitress 保持运行）。

---

## 五、日常运维

```bash
# 服务
systemctl status effic              # 查状态
systemctl restart effic             # 重启
systemctl enable effic              # 开机自启（安装脚本已 enable）

# 日志
tail -f /opt/effic/logs/gunicorn_error.log
tail -f /opt/effic/logs/app.log     # Flask RotatingFileHandler
journalctl -u effic -f              # systemd 层日志

# 健康检查
curl -s http://127.0.0.1:5050/healthz

# 代码更新（Windows 改完后同步）
bash /opt/effic/linux_deploy/scripts/sync_code.sh <win_ip> <win_user>
systemctl restart effic
```

## 六、定时备份（强烈建议）

备份凭据直接读 `/opt/effic/.env`，无需硬编码：

```bash
sudo tee /usr/local/bin/effic-backup >/dev/null <<'EOF'
#!/bin/bash
set -e
source /opt/effic/.env        # 自动加载 DB_USER / DB_PASSWORD / DB_DATABASE
BACKUP_DIR=/var/backups/effic/db
mkdir -p "$BACKUP_DIR"

# MySQL（单事务快照，不锁表）
mysqldump -u"$DB_USER" -p"$DB_PASSWORD" --single-transaction \
  --default-character-set=utf8mb4 "$DB_DATABASE" \
  | gzip > "$BACKUP_DIR/mysql_$(date +%F).sql.gz"

# SQLite（在线备份）
sqlite3 /opt/effic/kdd/express.db \
  ".backup '$BACKUP_DIR/express_$(date +%F).db'"

# 14 天前自动清理
find "$BACKUP_DIR" -type f -mtime +14 -delete
EOF
sudo chmod 755 /usr/local/bin/effic-backup

# 注册每日 cron
echo "0 2 * * * root /usr/local/bin/effic-backup >> /var/log/effic-backup.log 2>&1" \
  | sudo tee /etc/cron.d/effic-backup

# 立即手动跑一次验证
sudo /usr/local/bin/effic-backup && ls -lh /var/backups/effic/db/
```

## 七、日志切割

```bash
sudo tee /etc/logrotate.d/effic >/dev/null <<'EOF'
/opt/effic/logs/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

## 八、systemd 沙箱说明

`effic.service` 启用了 `ProtectSystem=strict` + `ReadWritePaths` 白名单：

```ini
ReadWritePaths=-/opt/effic/kdd      # express.db + backup + -journal + uploads + exports
                -/opt/effic/logs     # gunicorn + app 日志
                -/opt/effic/static/uploads
                -/opt/effic/__pycache__
```

前缀 `-` 表示路径不存在时忽略，避免 systemd 因 `226/NAMESPACE` 拒绝启动。

放开的目录**仅限业务必须写入的位置**，`/etc`、`/usr`、`/var`、`/home` 等仍只读，进程无 `CAP_SYS_ADMIN`，无提权空间。

**若新增可写目录**（比如新增上传路径），必须同步改 `effic.service`：

```bash
sudo nano /etc/systemd/system/effic.service
# 加一行 ReadWritePaths=...
sudo systemctl daemon-reload
sudo systemctl restart effic
```

---

## 附录 A：effic.service 完整定义

```ini
[Unit]
Description=effic Flask app (Gunicorn)
After=network.target mysql.service nginx.service
Requires=mysql.service

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/opt/effic
Environment=PATH=/opt/effic/venv/bin:/usr/bin:/bin
EnvironmentFile=-/opt/effic/.env

ExecStart=/opt/effic/venv/bin/gunicorn \
    --workers 4 --threads 2 --worker-class gthread \
    --bind 127.0.0.1:5050 --timeout 120 --graceful-timeout 30 \
    --max-requests 1000 --max-requests-jitter 100 \
    --access-logfile /opt/effic/logs/gunicorn_access.log \
    --error-logfile  /opt/effic/logs/gunicorn_error.log \
    --capture-output app:app

Restart=on-failure
RestartSec=3

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=-/opt/effic/kdd -/opt/effic/logs -/opt/effic/static/uploads -/opt/effic/__pycache__

[Install]
WantedBy=multi-user.target
```

## 附录 B：踩过的坑速查

| 现象 | 原因 | 解决 |
|------|------|------|
| apt 报 Permission denied | 忘 sudo | 整份脚本用 sudo 跑 |
| 解压后目录全乱 | zip 里是反斜杠路径 | 用 `unzip -l` 检查，反斜杠改 `7z x` |
| OSError: [Errno 30] Read-only file system | `kdd/` 目录被 systemd 挂只读 | `ReadWritePaths` 放开 `-kdd` 整个目录，不是只放开子目录 |
| ModuleNotFoundError: No module named 'aip' | requirements.txt 漏 baidu-aip | `sudo /opt/effic/venv/bin/pip install baidu-aip==4.16.13 chardet` |
| 日志时间差 8 小时 | 系统还是 UTC | `sudo timedatectl set-timezone Asia/Shanghai && systemctl restart effic` |
| `ufw enable` 后 SSH 断连 | 启用前没放行 22 | `sudo ufw allow 22/tcp` 必须先做 |
| `gunicorn_error.log` 里是旧 Traceback | 服务 auto-restart 时日志持续追加 | 重启前 `> gunicorn_error.log` 清空或 `tail -n` 看最新行 |
| 服务器重启后包丢了 | zip 放 /tmp 被清空 | 放 `/var/backups/effic/` 或 `/root/` |
| express.db 缺文件 | 只拷了 uploads 没拷 data 目录 | 确认 zip 里有 `data/express.db` 并已复制到 `/opt/effic/kdd/` |
