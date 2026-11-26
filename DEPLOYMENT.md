# 设备库存管理系统 - 完整部署手册

> **版本**: v1.0 | **更新日期**: 2026-11-26 | **适用环境**: Windows Server (IIS) / Ubuntu (Nginx + Gunicorn)

---

## 目录

- [一、项目概览](#一项目概览)
- [二、从 GitHub 拉取代码](#二从-github-拉取代码)
- [三、环境依赖](#三环境依赖)
- [四、方式 A：Windows + IIS 部署](#四方式-awindows--iis-部署)
- [五、方式 B：Ubuntu + Nginx + Gunicorn 部署](#五方式-bubuntu--nginx--gunicorn-部署)
- [六、配置说明](#六配置说明)
- [七、数据库初始化](#七数据库初始化)
- [八、Android APP 对接](#八android-app-对接)
- [九、日常运维](#九日常运维)
- [十、常见问题排查](#十常见问题排查)

---

## 一、项目概览

```
effic/                              ← 项目根目录（GitHub 仓库）
├── app.py                          ← 主 Flask 应用（库存、出入库）
├── express_routes.py               ← 快递单路由
├── auth_shared.py / crypto_util.py ← 认证 & 加密
├── init_database.py                ← 数据库初始化脚本
├── kdd/                            ← 快递单 OCR 模块（独立 Flask）
│   ├── app.py                      ← kdd 入口
│   ├── config.py                   ← kdd 配置
│   ├── ocr_engine.py               ← OCR 引擎（百度智能云 + Tesseract）
│   ├── templates/ static/          ← kdd 前端
│   ├── uploads/ exports/           ← 用户数据（不上传 GitHub）
│   └── express.db                  ← SQLite 数据库（不上传 GitHub）
├── templates/ static/              ← 主应用前端
├── linux_deploy/                   ← Linux 部署专用
│   ├── scripts/                    ← install_ubuntu.sh, sync_code.sh 等
│   ├── systemd/effic.service       ← systemd 服务定义
│   ├── nginx/effic.conf            ← Nginx 配置
│   └── docs/MIGRATE_LINUX.md       ← Linux 迁移详细手册
├── .env.example                    ← 环境变量模板
├── requirements.txt                ← Python 依赖
├── web.config                      ← IIS FastCGI 配置
└── .gitignore                      ← 排除 android_app/、.env、数据库等
```

**已排除内容（不在 GitHub 上）：**
- `android_app/` — Android 原生 APP 代码（单独维护）
- `.env` — 本地敏感配置
- `__pycache__/`、`*.pyc` — Python 缓存
- `kdd/uploads/`、`kdd/exports/`、`kdd/express.db` — 用户数据
- `logs/` — 运行日志

---

## 二、从 GitHub 拉取代码

```bash
# 克隆仓库（替换为实际 URL）
git clone https://github.com/diaojiang126/effic.git
cd effic

# 安装依赖
pip install -r requirements.txt

# 复制环境变量模板
copy .env.example .env
# Linux: cp .env.example .env

# 编辑 .env，修改以下关键项：
#   DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_DATABASE
#   SECRET_KEY (用 python -c "import secrets; print(secrets.token_hex(32))" 生成)
#   JWT_SECRET_KEY (同上)
```

---

## 三、环境依赖

| 组件 | 版本要求 | 备注 |
|------|----------|------|
| Python | 3.8+ | 推荐 3.12 |
| MySQL | 5.7+ 或 8.0+ | 主应用使用 |
| SQLite | 3.x | kdd 模块内嵌使用 |
| Tesseract OCR | 4.x | 快递单识别（可选百度智能云） |

**Python 依赖（核心）：**
```
Flask==2.3.3
pymysql==1.1.0
flask_jwt_extended==4.5.3
python-dotenv==1.0.0
requests==2.31.0
Pillow>=9.0.0
opencv-python-headless>=4.5.0
pytesseract==0.3.10
# Linux 额外需要: baidu-aip==4.16.13, gunicorn, waitress
```

---

## 四、方式 A：Windows + IIS 部署

### 4.1 环境准备

```powershell
# 1. 安装 Python（勾选 Add to PATH）
#    下载: https://www.python.org/downloads/

# 2. 安装 MySQL Server
#    下载: https://dev.mysql.com/downloads/mysql/

# 3. 启用 IIS 功能
#    控制面板 → 启用或关闭 Windows 功能 → 勾选:
#      - Internet Information Services
#      - 万维网服务 → 应用程序开发 → CGI
#      - Web 管理工具

# 4. 安装 wfastcgi
pip install wfastcgi
wfastcgi-enable
# 记录输出路径，如: C:\Python312\Lib\site-packages\wfastcgi.py
```

### 4.2 IIS 配置

**1. 创建应用程序池**
- 名称: `InventoryAppPool`
- .NET CLR 版本: **无托管代码**
- 托管管道模式: 集成

**2. 添加网站**
- 网站名称: `InventorySystem`
- 物理路径: `C:\web\effic`（或你的实际路径）
- 端口: `80`

**3. 配置 FastCGI**

打开 IIS 管理器 → 选择服务器节点 → **FastCGI 设置** → 添加：

| 配置项 | 值 |
|--------|-----|
| 完整路径 | `C:\Python312\python.exe`（按实际安装路径） |
| 参数 | `C:\Python312\Lib\site-packages\wfastcgi.py` |
| PYTHONPATH | `C:\web\effic` |
| WSGI_HANDLER | `app.app` |
| FLASK_ENV | `production` |

**4. 配置处理程序映射**

选择网站 → **处理程序映射** → 添加模块映射：

| 配置项 | 值 |
|--------|-----|
| 请求路径 | `*` |
| 模块 | `FastCgiModule` |
| 可执行文件 | `C:\Python312\python.exe|C:\Python312\Lib\site-packages\wfastcgi.py` |
| 名称 | `PythonFastCGI` |

点击"请求限制" → 勾选 **仅当请求映射到以下内容时才调用处理程序** → 选择 **文件或文件夹**。

### 4.3 web.config

项目根目录已有 `web.config`，确认 Python 路径正确：

```xml
<configuration>
    <system.webServer>
        <handlers>
            <add name="PythonFastCGI" path="*" verb="*"
                 modules="FastCgiModule"
                 scriptProcessor="C:\Python312\python.exe|C:\Python312\Lib\site-packages\wfastcgi.py"
                 resourceType="Unspecified" requireAccess="Script" />
        </handlers>
        <staticContent>
            <mimeMap fileExtension=".json" mimeType="application/json" />
        </staticContent>
    </system.webServer>
    <appSettings>
        <add key="PYTHONPATH" value="C:\web\effic" />
        <add key="WSGI_HANDLER" value="app.app" />
        <add key="WSGI_LOG" value="C:\web\effic\logs\wsgi.log" />
        <add key="FLASK_ENV" value="production" />
    </appSettings>
</configuration>
```

### 4.4 目录权限 & 防火墙

```powershell
# IIS_IUSRS 读写权限
icacls "C:\web\effic\logs" /grant "IIS_IUSRS:(OI)(CI)F"
icacls "C:\web\effic\kdd\uploads" /grant "IIS_IUSRS:(OI)(CI)F"
icacls "C:\web\effic\kdd\express.db" /grant "IIS_IUSRS:(OI)(CI)F"

# 防火墙
New-NetFirewallRule -DisplayName "HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow
```

### 4.5 一键部署脚本

项目提供 `deploy_iis.ps1`，以管理员身份运行：
```powershell
cd C:\web\effic
powershell -ExecutionPolicy Bypass -File deploy_iis.ps1
```

---

## 五、方式 B：Ubuntu + Nginx + Gunicorn 部署

> Linux 迁移详细步骤见 [linux_deploy/docs/MIGRATE_LINUX.md](linux_deploy/docs/MIGRATE_LINUX.md)

### 5.1 一键安装

```bash
# 克隆代码
git clone https://github.com/diaojiang126/effic.git /opt/effic
cd /opt/effic

# 生成 .env
cp .env.example .env
# 编辑 .env 填入实际配置

# 一键部署（必须 sudo）
cd linux_deploy/scripts
sudo bash install_ubuntu.sh
```

脚本自动完成：
1. 安装系统包（Python3 venv, MySQL, Nginx, Tesseract OCR）
2. 创建虚拟环境 + 安装依赖
3. 创建 MySQL 数据库和用户
4. 配置 systemd 服务 + Nginx 反代
5. 启动服务 + 防火墙

### 5.2 手动安装（可选）

```bash
# 系统依赖
sudo apt update
sudo apt install -y python3-venv python3-pip mysql-server nginx \
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng

# 虚拟环境
cd /opt/effic
python3 -m venv venv
source venv/bin/activate
pip install -r linux_deploy/config/requirements.txt

# 数据库
sudo mysql -e "CREATE DATABASE device_inventory_system CHARACTER SET utf8mb4;"
sudo mysql -e "CREATE USER 'ls'@'localhost' IDENTIFIED BY 'your_password';"
sudo mysql -e "GRANT ALL ON device_inventory_system.* TO 'ls'@'localhost';"

# systemd 服务
sudo cp linux_deploy/systemd/effic.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now effic

# Nginx
sudo cp linux_deploy/nginx/effic.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 防火墙
sudo ufw allow 80/tcp
```

### 5.3 验证

```bash
# 检查服务状态
sudo systemctl status effic

# Gunicorn 直连
curl -i http://127.0.0.1:5050/healthz

# Nginx 反代
curl -i http://127.0.0.1/healthz
# 期望: HTTP/1.1 200 OK + {"db":true,"ok":true}

# 时区设置（重要！否则日志时间差 8 小时）
sudo timedatectl set-timezone Asia/Shanghai
sudo systemctl restart effic
```

---

## 六、配置说明

### 6.1 环境变量 (.env)

```ini
# 数据库
DB_HOST=localhost
DB_PORT=3306
DB_USER=ls
DB_PASSWORD=your_password
DB_DATABASE=device_inventory_system

# 安全（必须修改为随机字符串）
SECRET_KEY=python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET_KEY=python -c "import secrets; print(secrets.token_hex(32))"

# 百度 OCR（可选，留空则用 Tesseract）
BAIDU_AIP_APP_ID=
BAIDU_AIP_API_KEY=
BAIDU_AIP_SECRET_KEY=

# 服务器
FLASK_ENV=production
HOST=0.0.0.0
PORT=5050
```

### 6.2 数据库初始化

```bash
# 自动创建表结构（应用启动时自动执行）
python init_database.py

# 或直接启动应用，首次访问时自动建表
python app.py
```

---

## 七、数据库初始化

```bash
# 方式 1: 脚本一键创建
python init_database.py

# 方式 2: 手动 SQL
mysql -u root -p <<EOF
CREATE DATABASE device_inventory_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'ls'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON device_inventory_system.* TO 'ls'@'localhost';
FLUSH PRIVILEGES;
EOF

# 创建管理员账户
python create_admin.py
# 按提示输入用户名、密码
```

---

## 八、Android APP 对接

Android APP 代码位于独立的 `android_app/` 目录（**未包含在本仓库中**）。

**APP 端仅需修改服务器地址：**

| 环境 | 地址 |
|------|------|
| Windows (IIS) | `http://<server_ip>:80` 或 `http://<server_ip>:5050` |
| Ubuntu (Nginx) | `http://<server_ip>` （Nginx 80 → Gunicorn 5050） |

灰度测试流程：
1. 一台测试手机改地址 → 登录 → 入库 → 出库 → OCR → ITS 全流程
2. 正常后全量推送

---

## 九、日常运维

### 9.1 Windows (IIS)

```powershell
# 重启应用
# IIS 管理器 → 应用程序池 → 右键 InventoryAppPool → 回收
# 或
& "C:\web\effic\restart_effic.bat"

# 查看日志
type C:\web\effic\logs\wsgi.log
type C:\web\effic\logs\app.log -Tail 100
```

### 9.2 Ubuntu (systemd)

```bash
# 服务管理
sudo systemctl status effic           # 状态
sudo systemctl restart effic          # 重启
sudo systemctl stop effic             # 停止

# 日志
sudo tail -f /opt/effic/logs/gunicorn_error.log
sudo tail -f /opt/effic/logs/app.log
sudo journalctl -u effic -f           # systemd 层日志

# 健康检查
curl -s http://127.0.0.1:5050/healthz

# 代码更新
cd /opt/effic && git pull
sudo systemctl restart effic
```

### 9.3 定时备份（Ubuntu）

```bash
# 注册每日备份
sudo tee /usr/local/bin/effic-backup >/dev/null <<'EOF'
#!/bin/bash
set -e
source /opt/effic/.env
BACKUP_DIR=/var/backups/effic/db
mkdir -p "$BACKUP_DIR"
mysqldump -u"$DB_USER" -p"$DB_PASSWORD" --single-transaction \
  --default-character-set=utf8mb4 "$DB_DATABASE" \
  | gzip > "$BACKUP_DIR/mysql_$(date +%F).sql.gz"
sqlite3 /opt/effic/kdd/express.db \
  ".backup '$BACKUP_DIR/express_$(date +%F).db'"
find "$BACKUP_DIR" -type f -mtime +14 -delete
EOF
sudo chmod 755 /usr/local/bin/effic-backup
echo "0 2 * * * root /usr/local/bin/effic-backup >> /var/log/effic-backup.log 2>&1" \
  | sudo tee /etc/cron.d/effic-backup
```

---

## 十、常见问题排查

### Windows (IIS)

| 现象 | 排查 |
|------|------|
| 500 错误，wfastcgi 找不到 | `pip show wfastcgi` 确认安装路径 |
| 数据库连接失败 | 检查 `.env` 或 `web.config` 中的连接配置 |
| 静态文件 404 | 处理程序映射中静态文件处理器应在 Python 之前 |
| 应用池自动停止 | 查看事件查看器 → Windows 日志 → 应用程序 |
| 外部无法访问 | 检查防火墙规则 + IIS 绑定 IP 设为 `*` |

### Ubuntu (systemd)

| 现象 | 排查 |
|------|------|
| 服务启动失败 | `journalctl -u effic -n 50` 看 Traceback |
| 502 Bad Gateway | Gunicorn 没起来 → `systemctl status effic` |
| OSError: Read-only file system | `effic.service` 的 `ReadWritePaths` 要放开 `kdd/` 整个目录 |
| ModuleNotFoundError: aip | `pip install baidu-aip==4.16.13 chardet` |
| 日志时间差 8 小时 | `sudo timedatectl set-timezone Asia/Shanghai` |
| express.db 权限 | `sudo chown www-data:www-data /opt/effic/kdd/express.db` |

### 通用

| 现象 | 排查 |
|------|------|
| 登录失败/JWT 过期 | 确认两台服务器 `.env` 的 `JWT_SECRET_KEY` 一致 |
| ITS 接口报错 | 检查 `its_service.py` 的 ITS 服务器可达性 + 凭据 |
| OCR 识别不准 | 百度 OCR 额度耗尽或 Tesseract 语言包未安装 |

---

## 附录 A：快速检查清单

部署完成后，逐项验证：

- [ ] `python --version` → 3.8+
- [ ] `mysql --version` → 5.7+
- [ ] `curl http://localhost/healthz` → `{"db":true,"ok":true}`
- [ ] 浏览器访问 `http://<server_ip>` → 登录页正常显示
- [ ] 登录 admin/admin123 → 修改密码
- [ ] 入库 → 出库 → 库存查询 → 报表 → 全流程通过
- [ ] 快递单拍照 → OCR 识别成功
- [ ] Android APP 改服务器地址 → 全功能正常
- [ ] 防火墙开放 80 端口
- [ ] 已配置定时备份

## 附录 B：登录凭据

| 环境 | 用户名 | 密码 | 说明 |
|------|--------|------|------|
| 首次部署 | admin | admin123 | **必须立即修改！** |

修改方式：登录后进入"用户管理" → 编辑 admin → 设置新密码。

---

*详细 IIS 部署见 `DEPLOYMENT_IIS.md`，Linux 迁移见 `linux_deploy/docs/MIGRATE_LINUX.md`*
