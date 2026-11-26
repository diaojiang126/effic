# 设备库存管理系统 - IIS部署手册

## 目录
1. [环境要求](#环境要求)
2. [安装步骤](#安装步骤)
3. [数据库配置](#数据库配置)
4. [IIS配置](#iis配置)
5. [常见问题](#常见问题)

---

## 环境要求

- Windows Server 2016+ 或 Windows 10/11
- Python 3.8+
- MySQL 5.7+ 或 MySQL 8.0+
- IIS 10.0+
- URL Rewrite Module (可选)

---

## 安装步骤

### 1. 安装Python

1. 下载Python 3.8+：https://www.python.org/downloads/
2. 安装时勾选 "Add Python to PATH"
3. 验证安装：
```powershell
python --version
pip --version
```

### 2. 安装MySQL

1. 下载MySQL：https://dev.mysql.com/downloads/mysql/
2. 安装MySQL Server
3. 记住root密码

### 3. 安装IIS

1. 打开"控制面板" > "程序" > "启用或关闭Windows功能"
2. 勾选以下功能：
   - Internet Information Services
   - Web管理工具
   - 万维网服务
   - 应用程序开发功能 > CGI

### 4. 安装wfastcgi

```powershell
pip install wfastcgi
wfastcgi-enable
```

记录输出的路径，例如：
```
C:\Python39\Lib\site-packages\wfastcgi.py
```

---

## 数据库配置

### 方式一：使用初始化脚本（推荐）

1. 以管理员身份运行PowerShell
2. 执行初始化脚本：

```powershell
cd C:\web\effic
python init_database.py
```

### 方式二：手动创建

1. 登录MySQL：
```powershell
mysql -u root -p
```

2. 执行以下SQL：
```sql
-- 创建数据库
CREATE DATABASE device_inventory_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 创建用户（请修改密码）
CREATE USER 'ls'@'localhost' IDENTIFIED BY 'ls1213456';

-- 授权
GRANT ALL PRIVILEGES ON device_inventory_system.* TO 'ls'@'localhost';
FLUSH PRIVILEGES;

-- 验证
SHOW DATABASES;
```

3. 应用启动时会自动创建表结构

---

## IIS配置

### 1. 创建应用程序池

1. 打开IIS管理器
2. 右键"应用程序池" > "添加应用程序池"
3. 配置：
   - 名称：`InventoryAppPool`
   - .NET CLR版本：`无托管代码`
   - 托管管道模式：`集成`

### 2. 创建网站

1. 右键"网站" > "添加网站"
2. 配置：
   - 网站名称：`InventorySystem`
   - 应用程序池：`InventoryAppPool`
   - 物理路径：`C:\web\effic`
   - 端口：`80`（或其他可用端口）

### 3. 配置FastCGI

1. 选择网站 > "处理程序映射"
2. 右键"添加模块映射"
3. 配置：
   - 请求路径：`*`
   - 模块：`FastCgiModule`
   - 可执行文件：`C:\Python39\python.exe|C:\Python39\Lib\site-packages\wfastcgi.py`
   - 名称：`PythonFastCGI`

4. 点击"请求限制" > 勾选"仅当请求映射到以下内容时才调用处理程序" > 选择"文件或文件夹"

### 4. 配置FastCGI设置

1. 选择服务器节点 > "FastCGI设置"
2. 双击添加应用程序
3. 配置：
   - 完整路径：`C:\Python39\python.exe`
   - 参数：`C:\Python39\Lib\site-packages\wfastcgi.py`

4. 环境变量（点击"..."添加）：
   ```
   PYTHONPATH = C:\web\effic
   WSGI_HANDLER = app.app
   FLASK_ENV = production
   ```

### 5. 配置web.config

在应用目录创建 `web.config` 文件（见下方）

---

## 配置文件

### web.config

将此文件放在应用根目录：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <system.webServer>
        <handlers>
            <add name="PythonFastCGI" 
                 path="*" 
                 verb="*" 
                 modules="FastCgiModule" 
                 scriptProcessor="C:\Python39\python.exe|C:\Python39\Lib\site-packages\wfastcgi.py" 
                 resourceType="Unspecified" 
                 requireAccess="Script" />
        </handlers>
        <staticContent>
            <mimeMap fileExtension=".json" mimeType="application/json" />
            <mimeMap fileExtension=".woff" mimeType="font/woff" />
            <mimeMap fileExtension=".woff2" mimeType="font/woff2" />
        </staticContent>
        <httpErrors errorMode="Detailed" />
    </system.webServer>
    <appSettings>
        <add key="PYTHONPATH" value="C:\web\effic" />
        <add key="WSGI_HANDLER" value="app.app" />
        <add key="WSGI_LOG" value="C:\web\effic\logs\wsgi.log" />
        <add key="FLASK_ENV" value="production" />
    </appSettings>
</configuration>
```

**注意**：请根据实际Python安装路径修改 `scriptProcessor`

---

## 部署检查清单

- [ ] Python已安装并添加到PATH
- [ ] MySQL已安装并运行
- [ ] 数据库已创建
- [ ] 数据库用户已创建并授权
- [ ] wfastcgi已安装并启用
- [ ] IIS已安装CGI模块
- [ ] 应用程序池已创建（无托管代码）
- [ ] 网站已创建
- [ ] FastCGI处理程序已配置
- [ ] web.config已放置
- [ ] 静态文件目录权限已设置
- [ ] logs目录已创建

---

## 目录权限设置

确保IIS_IUSRS对以下目录有读写权限：

```
C:\web\effic\logs
C:\web\effic\static
C:\web\effic\templates
```

PowerShell命令：
```powershell
icacls "C:\web\effic\logs" /grant "IIS_IUSRS:(OI)(CI)F"
```

---

## 常见问题

### 1. 500错误 - 无法加载wfastcgi

检查Python路径是否正确，确保wfastcgi已安装：
```powershell
pip show wfastcgi
```

### 2. 数据库连接失败

检查app.py中的数据库配置：
```python
host='localhost'
user='ls'
password='ls1213456'
database='device_inventory_system'
```

### 3. 静态文件无法加载

检查静态文件处理程序映射，确保在Python处理程序之前。

### 4. 权限问题

确保应用程序池标识对应用目录有读取权限。

### 5. 本地可访问，外部无法访问

**检查防火墙：**
```powershell
# 添加防火墙入站规则（以管理员身份运行）
New-NetFirewallRule -DisplayName "HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow
New-NetFirewallRule -DisplayName "HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
```

**检查IIS绑定：**
1. 打开IIS管理器
2. 选择网站 → 绑定
3. 确保IP地址为 `*` 或 `0.0.0.0`（不是 `127.0.0.1`）

**检查Windows防火墙：**
1. 控制面板 → Windows Defender 防火墙
2. 高级设置 → 入站规则
3. 确保允许HTTP/HTTPS端口

**检查服务器安全组（云服务器）：**
- 阿里云/腾讯云/AWS等需要在控制台开放80/443端口

### 6. IIS应用程序池自动停止

检查事件查看器中的错误日志，常见原因：
- Python路径不正确
- wfastcgi未正确安装
- 权限不足

---

## 登录信息

部署完成后，使用以下凭据登录：

- **用户名**: admin
- **密码**: admin123

**重要**：首次登录后请立即修改密码！

---

## 技术支持

如有问题，请检查：
1. IIS日志：`C:\inetpub\logs\LogFiles`
2. 应用日志：`C:\web\effic\logs\wsgi.log`
3. 事件查看器：Windows日志 > 应用程序
