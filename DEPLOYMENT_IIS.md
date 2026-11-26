# IIS部署手册

## 1. 环境准备

### 1.1 安装Python

1. 从 [Python官网](https://www.python.org/downloads/windows/) 下载最新的Python Windows安装包（推荐使用Python 3.11+）
2. 运行安装包，勾选「Add Python to PATH」选项
3. 选择「Customize installation」，确保安装「pip」和「tcl/tk and IDLE」
4. 在「Advanced Options」中，勾选「Install for all users」和「Add Python to environment variables」
5. 完成安装后，打开命令提示符，运行 `python --version` 和 `pip --version` 验证安装成功

### 1.2 安装wfastcgi

wfastcgi是IIS与Python之间的桥梁，用于处理FastCGI请求：

```bash
pip install wfastcgi
```

安装完成后，启用wfastcgi：

```bash
wfastcgi-enable
```

记录下输出的配置信息，例如：
```
c:\python311\python.exe|c:\python311\lib\site-packages\wfastcgi.py
```

### 1.3 安装IIS和必要模块

1. 打开「控制面板」→「程序和功能」→「启用或关闭Windows功能」
2. 勾选以下选项：
   - Internet Information Services
     - Web Management Tools
       - IIS Management Console
     - World Wide Web Services
       - Application Development Features
         - CGI
         - FastCGI
         - ISAPI Extensions
         - ISAPI Filters
       - Common HTTP Features
         - Default Document
         - Directory Browsing
         - HTTP Errors
         - Static Content
       - Health and Diagnostics
         - HTTP Logging
       - Security
         - Request Filtering
         - Basic Authentication（可选，根据需求）
3. 点击「确定」安装，等待安装完成
4. 打开「IIS管理器」验证安装成功

### 1.4 安装URL Rewrite模块

1. 从 [Microsoft官网](https://www.iis.net/downloads/microsoft/url-rewrite) 下载URL Rewrite模块
2. 运行安装包，完成安装
3. 重启IIS管理器，验证「URL Rewrite」功能已添加到IIS管理器中

## 2. 项目部署

### 2.1 复制项目文件到服务器

1. 在服务器上创建项目目录，例如：`C:\web\effic`
2. 复制以下文件和目录到服务器项目目录：
   - app.py
   - requirements.txt
   - web.config
   - templates/ 目录
   - static/ 目录（如果存在）
   - logs/ 目录（如果不存在，请创建）

### 2.2 安装项目依赖

1. 打开命令提示符，切换到项目目录：
   ```bash
   cd C:\web\effic
   ```
2. 安装项目依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 验证依赖安装成功

### 2.3 配置web.config文件

根据服务器环境修改web.config文件：

1. 打开 `C:\web\effic\web.config` 文件
2. 修改FastCGI处理程序路径，使用步骤1.2中记录的wfastcgi配置：
   ```xml
   <add name="Python FastCGI" path="*" verb="*" modules="FastCgiModule" scriptProcessor="C:\Python311\python.exe|C:\Python311\Lib\site-packages\wfastcgi.py" resourceType="Unspecified" requireAccess="Script" preCondition="integratedMode" />
   ```
3. 修改PYTHONPATH为项目实际路径：
   ```xml
   <add key="PYTHONPATH" value="C:\web\effic" />
   ```
4. 修改WSGI_LOG为实际日志路径：
   ```xml
   <add key="WSGI_LOG" value="C:\web\effic\logs\app.log" />
   ```
5. 保存并关闭web.config文件

### 2.4 创建IIS站点

1. 打开「IIS管理器」
2. 右键点击「站点」→「添加网站」
3. 填写站点信息：
   - 站点名称：`effic`
   - 物理路径：`C:\web\effic`
   - 绑定：
     - 类型：HTTP
     - IP地址：All Unassigned 或指定IP
     - 端口：80（或其他可用端口）
     - 主机名：（可选，域名访问时必填，例如：`yourdomain.com`）
4. 点击「确定」创建站点

## 2.5 配置域名访问

### 2.5.1 域名解析设置

1. 登录您的域名注册商控制台
2. 找到「域名解析」或「DNS管理」选项
3. 添加以下解析记录：
   - **A记录**：
     - 主机记录：`@`（表示根域名，例如：`yourdomain.com`）
     - 记录类型：`A`
     - 解析线路：`默认`或`全网`
     - 记录值：您的服务器公网IP地址
     - TTL：`600`（或默认值）
   - **www记录**（可选）：
     - 主机记录：`www`
     - 记录类型：`A`
     - 解析线路：`默认`或`全网`
     - 记录值：您的服务器公网IP地址
     - TTL：`600`（或默认值）
4. 保存解析设置，等待DNS生效（通常需要1-10分钟）

### 2.5.2 IIS站点绑定设置

1. 打开「IIS管理器」
2. 找到并右键点击您的站点（例如：`effic`）
3. 选择「编辑绑定」
4. 在弹出的「网站绑定」窗口中，点击「添加」
5. 填写绑定信息：
   - 类型：`HTTP`
   - IP地址：`All Unassigned` 或指定服务器IP
   - 端口：`80`
   - 主机名：`yourdomain.com`（填写您的实际域名）
6. 点击「确定」保存绑定
7. （可选）如果需要添加www域名，重复步骤4-6，主机名填写为`www.yourdomain.com`

### 2.5.3 配置HTTPS（推荐）

1. 申请SSL证书
   - 可以从域名注册商、云服务商或免费证书服务（如Let's Encrypt）获取
   - 保存证书文件（通常包含`.cer`和`.pfx`或`.key`文件）

2. 安装SSL证书到服务器
   - 打开「控制面板」→「管理工具」→「Internet 信息服务(IIS)管理器」
   - 在左侧导航栏中，点击服务器名称
   - 双击「服务器证书」图标
   - 点击右侧「导入」链接
   - 浏览并选择您的证书文件（`.pfx`格式）
   - 输入证书密码（如果有）
   - 选择「个人」证书存储
   - 点击「确定」完成导入

3. 配置HTTPS绑定
   - 回到站点「编辑绑定」窗口
   - 点击「添加」
   - 填写绑定信息：
     - 类型：`HTTPS`
     - IP地址：`All Unassigned` 或指定服务器IP
     - 端口：`443`
     - 主机名：`yourdomain.com`
     - SSL证书：选择您刚安装的证书
   - 点击「确定」保存绑定
   - （可选）重复步骤添加`www.yourdomain.com`的HTTPS绑定

4. 配置HTTP重定向到HTTPS（推荐）
   - 在IIS管理器中，选择您的站点
   - 双击「HTTP重定向」图标
   - 勾选「将请求重定向到目标」
   - 输入目标URL：`https://yourdomain.com$S$Q`
   - 勾选「将所有请求重定向到确切的目标（而不是相对于目标）」
   - 在「状态代码」下拉菜单中，选择「301 - 永久移动」
   - 点击「应用」保存设置

### 2.5.4 配置URL重写规则

如果您的应用使用了友好URL（如`/categories`而不是`/categories.html`），请确保URL重写模块已正确配置：

1. 在IIS管理器中，选择您的站点
2. 双击「URL重写」图标
3. 确保已存在Flask路由重写规则：
   - 名称：`Flask Routes`
   - 匹配URL：`.*`
   - 条件：只有当请求的文件或目录不存在时才重写
   - 操作：重写到`app.py`
4. 如果不存在该规则，请点击「添加规则」→「空白规则」手动创建

### 2.5.5 测试域名访问

1. 确保域名解析已生效（可以使用`nslookup yourdomain.com`命令验证）
2. 打开浏览器，访问：
   - HTTP：`http://yourdomain.com`
   - HTTPS（如果已配置）：`https://yourdomain.com`
3. 如果看到应用页面，说明域名访问配置成功
4. 检查应用功能是否正常，包括API调用、静态文件加载等

## 2.6 配置站点权限

1. 右键点击刚创建的站点，选择「编辑权限」
2. 切换到「安全」选项卡，点击「编辑」→「添加」
3. 输入「IIS_IUSRS」，点击「检查名称」→「确定」
4. 为「IIS_IUSRS」用户分配「修改」权限，点击「确定」保存

### 2.6 测试应用

1. 打开浏览器，访问站点URL，例如：`http://localhost`
2. 如果看到应用页面，说明部署成功
3. 检查日志文件 `C:\web\effic\logs\app.log`，确保没有错误

## 3. 配置MySQL数据库

### 3.1 安装MySQL

1. 从 [MySQL官网](https://dev.mysql.com/downloads/installer/) 下载MySQL Installer
2. 运行安装包，选择「Developer Default」或「Server Only」安装类型
3. 按照安装向导完成安装，设置root密码
4. 启动MySQL服务

### 3.2 创建数据库和用户

1. 打开MySQL命令行工具或MySQL Workbench
2. 创建数据库：
   ```sql
   CREATE DATABASE inventory_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```
3. 创建用户并授权：
   ```sql
   CREATE USER 'effic_user'@'localhost' IDENTIFIED BY 'your_password';
   GRANT ALL PRIVILEGES ON inventory_system.* TO 'effic_user'@'localhost';
   FLUSH PRIVILEGES;
   ```

### 3.3 配置应用数据库连接

1. 打开 `C:\web\effic\app.py` 文件
2. 修改数据库连接信息：
   ```python
   def __init__(self, host='localhost', user='effic_user', password='your_password', database='inventory_system'):
   ```
3. 保存并关闭app.py文件

## 4. 常见问题及解决方案

### 4.1 500 Internal Server Error

**问题**：访问站点时出现500错误

**解决方案**：
1. 检查IIS日志，位于 `C:\inetpub\logs\LogFiles\W3SVC1`
2. 检查Python日志，位于 `C:\web\effic\logs\app.log`
3. 确保wfastcgi已正确安装和启用
4. 确保Python解释器路径正确
5. 确保项目依赖已正确安装

### 4.2 404 Not Found

**问题**：访问站点时出现404错误

**解决方案**：
1. 确保URL重写模块已正确安装
2. 检查web.config中的重写规则配置
3. 确保站点物理路径正确
4. 确保app.py文件存在于站点根目录

### 4.3 数据库连接失败

**问题**：应用无法连接到MySQL数据库

**解决方案**：
1. 确保MySQL服务已启动
2. 检查app.py中的数据库连接信息是否正确
3. 确保MySQL用户有足够的权限
4. 确保MySQL服务器允许远程连接（如果需要）
5. 检查防火墙设置，确保3306端口已开放

### 4.4 静态文件无法访问

**问题**：CSS、JS等静态文件无法访问

**解决方案**：
1. 确保static目录存在于站点根目录
2. 检查web.config中的静态文件配置
3. 确保IIS_IUSRS用户对static目录有读取权限

## 5. 维护与监控

### 5.1 日志管理

1. 定期检查应用日志：`C:\web\effic\logs\app.log`
2. 定期检查IIS日志：`C:\inetpub\logs\LogFiles\W3SVC1`
3. 配置日志轮转，避免日志文件过大

### 5.2 应用更新

1. 停止IIS站点
2. 备份项目文件
3. 复制新的项目文件到服务器
4. 安装新的依赖（如果有）
5. 启动IIS站点
6. 测试应用是否正常运行

### 5.3 性能监控

1. 使用IIS管理器的「Worker Processes」功能监控应用性能
2. 使用Windows性能监视器监控CPU、内存使用情况
3. 定期检查数据库性能，优化查询语句

## 6. 安全建议

1. 定期更新Python和依赖包，修复安全漏洞
2. 配置强密码策略，保护数据库和服务器
3. 启用HTTPS，保护数据传输安全
4. 限制IIS站点的访问IP范围（如果需要）
5. 定期备份数据库和项目文件
6. 禁用不必要的IIS模块和功能
7. 配置适当的错误页面，避免泄露敏感信息

## 7. 附录

### 7.1 常用命令

- 启动/停止IIS站点：
  ```bash
  iisreset /start
  iisreset /stop
  ```

- 查看Python版本：
  ```bash
  python --version
  ```

- 查看pip版本：
  ```bash
  pip --version
  ```

- 安装依赖：
  ```bash
  pip install -r requirements.txt
  ```

- 启用wfastcgi：
  ```bash
  wfastcgi-enable
  ```

- 禁用wfastcgi：
  ```bash
  wfastcgi-disable
  ```

### 7.2 参考链接

- [Python官方文档](https://docs.python.org/3/)
- [Flask官方文档](https://flask.palletsprojects.com/)
- [IIS官方文档](https://docs.microsoft.com/en-us/iis/)
- [wfastcgi GitHub](https://github.com/microsoft/IISFastCGI)
- [MySQL官方文档](https://dev.mysql.com/doc/)