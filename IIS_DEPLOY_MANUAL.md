# 设备库存管理系统 - IIS 部署手册

## 系统概述

| 项目 | 说明 |
|------|------|
| 框架 | Flask 2.3.3 (Python) |
| 数据库 | MySQL（主系统）+ SQLite（快递单识别） |
| Web 服务器 | IIS + wfastcgi |
| 默认端口 | 5050 |
| Python 版本 | 3.9+（建议 3.11/3.12） |

---

## 一、环境要求

### 1.1 Windows 功能
- **IIS**（Internet Information Services）
- **Application Development** → **CGI** 功能

**启用方法（管理员 PowerShell）：**
```powershell
Install-WindowsFeature -Name Web-Server, Web-CGI -IncludeAllSubFeature
```

### 1.2 软件要求
| 软件 | 说明 |
|------|------|
| Python 3.9+ | [python.org](https://www.python.org/downloads/) — 安装时勾选 **Add to PATH** |
| MySQL 5.7+ | 主系统数据库（[MySQL Installer](https://dev.mysql.com/downloads/installer/)）|
| pip 依赖 | 见下方第三节 |

### 1.3 目录结构
项目路径 `C:\luosen\effic`（可按实际位置调整）：
```
C:\luosen\effic\
├── app.py                 # Flask 主入口
├── express_routes.py      # 快递单子系统路由
├── web.config             # IIS 配置文件
├── requirements.txt       # 主系统依赖
├── deploy_iis.ps1         # 自动部署脚本
├── init_database.py       # 数据库初始化
├── static/                # 静态文件
├── templates/             # 页面模板
├── uploads/               # 上传目录
├── logs/                  # 日志目录
└── kdd/                   # 快递单子系统
    ├── express.db         # SQLite 数据库
    ├── requirements.txt   # 子系统依赖
    └── uploads/           # 子系统上传目录
```

---

## 二、快速部署（推荐）

### 管理员身份运行 PowerShell

```powershell
# 1. 进入项目目录
cd C:\luosen\effic

# 2. 执行部署脚本（自动完成 8 个步骤）
.\deploy_iis.ps1

# 可选：指定端口和数据库名称
.\deploy_iis.ps1 -SitePort 8080 -DbName device_inventory_system
```

脚本会自动完成：
1. 检测 Python 环境
2. 安装 wfastcgi
3. 注册 FastCGI 到 IIS
4. 安装项目依赖
5. 创建 logs/uploads 目录
6. 生成 web.config
7. 创建 IIS 站点和应用程序池
8. 初始化 MySQL 数据库

### 部署验证
打开浏览器访问：`http://localhost:5050`

---

## 三、手动部署步骤

如果快速部署脚本不适用，可按以下步骤手动部署。

### 3.1 安装 Python 依赖

```powershell
cd C:\luosen\effic

# 主系统依赖
pip install -r requirements.txt

# 快递子系统依赖
pip install -r kdd\requirements.txt
```

### 3.2 安装并配置 wfastcgi

```powershell
# 安装 wfastcgi
pip install wfastcgi

# 注册到 IIS（管理员 PowerShell）
python C:\Python312\Lib\site-packages\wfastcgi.py /install
```

### 3.3 创建目录及设置权限

```powershell
mkdir logs uploads kdd\uploads kdd\exports -Force

# 授予 IIS_IUSRS 写权限
icacls logs /grant "IIS_IUSRS:(CI)(OI)M" /q
icacls uploads /grant "IIS_IUSRS:(CI)(OI)M" /q
icacls kdd\uploads /grant "IIS_IUSRS:(CI)(OI)M" /q
```

### 3.4 配置 web.config

将以下内容保存为 `C:\luosen\effic\web.config`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <system.webServer>
        <handlers>
            <add name="PythonFastCGI"
                 path="*"
                 verb="*"
                 modules="FastCgiModule"
                 scriptProcessor="C:\Python312\python.exe|C:\Python312\Lib\site-packages\wfastcgi.py"
                 resourceType="Unspecified"
                 requireAccess="Script" />
        </handlers>
        <staticContent>
            <remove fileExtension=".json" />
            <mimeMap fileExtension=".json" mimeType="application/json" />
            <remove fileExtension=".woff" />
            <mimeMap fileExtension=".woff" mimeType="font/woff" />
            <remove fileExtension=".woff2" />
            <mimeMap fileExtension=".woff2" mimeType="font/woff2" />
        </staticContent>
        <httpErrors errorMode="Detailed" />
        <directoryBrowse enabled="false" />
        <security>
            <requestFiltering>
                <requestLimits maxAllowedContentLength="10485760" />
            </requestFiltering>
        </security>
        <modules>
            <remove name="WebDAVModule" />
        </modules>
    </system.webServer>
    <appSettings>
        <add key="PYTHONPATH" value="C:\luosen\effic" />
        <add key="WSGI_HANDLER" value="app.app" />
        <add key="WSGI_LOG" value="C:\luosen\effic\logs\wsgi.log" />
        <add key="FLASK_ENV" value="production" />
        <add key="DATABASE_NAME" value="device_inventory_system" />
    </appSettings>
</configuration>
```

> **注意**：`scriptProcessor` 中的 `python.exe` 路径必须与实际安装路径一致

### 3.5 创建 IIS 站点

**方式一：使用 PowerShell**

```powershell
Import-Module WebAdministration

# 创建应用程序池
New-WebAppPool -Name "luosen"
Set-ItemProperty "IIS:\AppPools\luosen" managedRuntimeVersion ""
Set-ItemProperty "IIS:\AppPools\luosen" enable32BitAppOnWin64 $false
Set-ItemProperty "IIS:\AppPools\luosen" processModel.identityType ApplicationPoolIdentity

# 创建网站
New-Website -Name "luosen" -PhysicalPath "C:\luosen\effic" -Port 5050
Set-ItemProperty "IIS:\Sites\luosen" applicationPool "luosen"
Start-Website "luosen"
```

**方式二：使用 IIS 管理器**
1. 打开 **IIS 管理器**
2. 右键 **网站** → **添加网站**
3. 网站名称：`luosen`
4. 物理路径：`C:\luosen\effic`
5. 端口：`5050`
6. 确定后，选中该站点 → **基本设置** → **应用程序池** → 选择 `luosen`

### 3.6 初始化数据库

```powershell
# 确保 MySQL 已运行，然后执行：
python init_database.py

# 如果只需要建表，可以手动导入：
mysql -u root -p device_inventory_system < database_schema.sql
```

### 3.7 配置应用程序池标识权限

1. **IIS 管理器** → **应用程序池** → 右键 `luosen` → **高级设置**
2. **进程模型** → **标识** → 选择 `ApplicationPoolIdentity`
3. 确保以下目录对 `IIS AppPool\luosen` 有读写权限：
   - `C:\luosen\effic\logs`
   - `C:\luosen\effic\uploads`
   - `C:\luosen\effic\kdd\uploads`

---

## 四、验证部署

| 检查项 | 说明 |
|--------|------|
| 访问首页 | `http://localhost:5050` |
| 登录页面 | `http://localhost:5050/login` |
| 快递单识别 | `http://localhost:5050/express` |
| API 测试 | `http://localhost:5050/api/public/companies` |

---

## 五、排错指南

### 5.1 500 Internal Server Error

```powershell
# 查看 WSGI 日志
Get-Content C:\luosen\effic\logs\wsgi.log -Tail 50
```

### 5.2 403 Forbidden

- 检查应用程序池标识是否正确
- 确认 IIS 功能中已安装 **CGI** 角色服务

### 5.3 静态文件加载异常（CSS/JS 404）

- 确认 web.config 中已配置 MIME 类型
- 确认站点物理路径指向 `C:\luosen\effic`（含 static 目录）

### 5.4 数据库连接失败

```powershell
# 测试 MySQL 连接
mysql -u root -p -e "SHOW DATABASES;"
```

### 5.5 重新部署

```powershell
# 重新运行部署脚本
.\deploy_iis.ps1
```

### 5.6 查看 Python FastCGI 是否注册

```powershell
# 在 IIS 管理器中检查：
# 服务器节点 → FastCGI 设置 → 应能看到 Python wfastcgi 条目
```

---

## 六、常用命令参考

```powershell
# 重启 IIS 站点
Restart-WebAppPool luosen
Restart-Website luosen

# 查看 IIS 站点列表
Get-Website

# 查看应用程序池
Get-WebAppPool

# 停止/启动站点
Stop-Website luosen
Start-Website luosen

# 查看 Windows 事件日志
Get-EventLog -LogName System -Source "W3SVC" -Newest 10
Get-EventLog -LogName Application -Source "Python" -Newest 10
```

---

## 七、Android App 连接配置

部署完成后，修改 APK 中的后端地址：

文件：`android_app/app/src/main/java/com/inventory/app/data/api/RetrofitClient.kt`

```kotlin
// 将 IP 地址改为服务器的实际 IP
private const val BASE_URL = "http://SERVER_IP:5050/api/"
```

然后重新打包 APK。

---

> 📄 **相关文件：**
> - 自动部署脚本：[deploy_iis.ps1](file:///c:/web/effic/deploy_iis.ps1)
> - IIS 配置：[web.config](file:///c:/web/effic/web.config)
> - 项目入口：[app.py](file:///c:/web/effic/app.py)
> - 数据库初始化：[init_database.py](file:///c:/web/effic/init_database.py)
