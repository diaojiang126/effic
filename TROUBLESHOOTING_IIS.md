# IIS部署后无法访问排查指南

## 1. 检查IIS服务状态

### 1.1 确认IIS服务正在运行

1. 打开「控制面板」→「管理工具」→「服务」
2. 查找「World Wide Web Publishing Service」服务
3. 确保服务状态为「正在运行」，启动类型为「自动」
4. 如果服务未运行，右键点击「启动」

### 1.2 检查IIS管理器

1. 打开「IIS管理器」
2. 确认站点状态为「已启动」
3. 如果站点未启动，右键点击「启动」

## 2. 检查站点绑定配置

### 2.1 验证站点绑定

1. 在IIS管理器中，选择您的站点
2. 右键点击「编辑绑定」
3. 检查绑定配置：
   - 类型：HTTP 或 HTTPS
   - IP地址：确保不是错误的IP
   - 端口：80（HTTP）或 443（HTTPS）
   - 主机名：如果设置了域名，确保正确
4. 确认没有其他站点使用相同的IP+端口组合

### 2.2 测试本地访问

1. 登录到服务器
2. 打开浏览器
3. 尝试访问：
   - `http://localhost`
   - `http://127.0.0.1`
   - `http://服务器IP`
   - `http://域名`（如果已配置）
4. 观察是否可以访问

## 3. 检查防火墙和网络设置

### 3.1 检查Windows防火墙

1. 打开「控制面板」→「Windows Defender 防火墙」→「允许应用或功能通过Windows Defender防火墙」
2. 确保「万维网服务(HTTP)」和「万维网服务(HTTPS)」已勾选
3. 如果未勾选，点击「允许其他应用」，浏览并添加

### 3.2 检查端口开放状态

1. 打开命令提示符
2. 运行命令检查80端口：
   ```bash
   netstat -an | findstr :80
   ```
3. 运行命令检查443端口：
   ```bash
   netstat -an | findstr :443
   ```
4. 确认端口处于「LISTENING」状态

### 3.3 检查网络连接

1. 从客户端计算机ping服务器IP，确认网络连通
   ```bash
   ping 服务器IP
   ```
2. 测试端口连通性（需要安装telnet或使用PowerShell）：
   ```bash
   # 使用PowerShell测试HTTP端口
   Test-NetConnection -ComputerName 服务器IP -Port 80
   ```

## 4. 检查权限设置

### 4.1 站点目录权限

1. 右键点击站点目录（例如：`C:\web\effic`）→「属性」→「安全」
2. 确保「IIS_IUSRS」用户存在于权限列表中
3. 确保「IIS_IUSRS」用户具有以下权限：
   - 读取 & 执行
   - 列出文件夹内容
   - 读取
   - 写入（如果应用需要写入权限）

### 4.2 检查应用池身份

1. 在IIS管理器中，点击「应用池」
2. 找到您站点对应的应用池（通常与站点同名）
3. 右键点击「高级设置」
4. 检查「标识」属性，默认应为「ApplicationPoolIdentity」
5. 确保应用池状态为「已启动」

## 5. 检查wfastcgi配置

### 5.1 确认wfastcgi已启用

1. 打开命令提示符，运行：
   ```bash
   wfastcgi-enable
   ```
2. 如果返回错误，重新安装wfastcgi：
   ```bash
   pip install wfastcgi
   wfastcgi-enable
   ```
3. 记录输出的配置信息，例如：
   ```
   c:\python313\python.exe|c:\python313\lib\site-packages\wfastcgi.py
   ```

### 5.2 检查web.config中的wfastcgi配置

1. 打开 `C:\web\effic\web.config` 文件
2. 确认FastCGI处理程序路径正确：
   ```xml
   <add name="Python FastCGI" path="*" verb="*" modules="FastCgiModule" scriptProcessor="C:\Python313\python.exe|C:\Python313\Lib\site-packages\wfastcgi.py" resourceType="Unspecified" requireAccess="Script" preCondition="integratedMode" />
   ```
3. 确保路径与wfastcgi-enable输出的一致

## 6. 检查应用配置

### 6.1 验证Python解释器路径

1. 打开命令提示符，运行：
   ```bash
   where python
   ```
2. 确认输出的Python路径与web.config中的一致

### 6.2 检查PYTHONPATH配置

1. 打开web.config文件
2. 确认PYTHONPATH指向正确的项目目录：
   ```xml
   <add key="PYTHONPATH" value="C:\web\effic" />
   ```

### 6.3 检查WSGI_HANDLER配置

1. 打开web.config文件
2. 确认WSGI_HANDLER配置正确：
   ```xml
   <add key="WSGI_HANDLER" value="app.app" />
   ```
   （注意：第一个"app"是模块名（app.py），第二个"app"是Flask应用实例名）

## 7. 检查日志文件

### 7.1 检查IIS日志

1. 默认IIS日志位置：`C:\inetpub\logs\LogFiles\W3SVC1`
2. 找到最新的日志文件，查看错误信息
3. 常见错误代码：
   - 404：文件未找到
   - 500：服务器内部错误
   - 502：网关错误（通常是wfastcgi配置问题）
   - 503：服务不可用

### 7.2 检查应用日志

1. 检查web.config中配置的日志路径：
   ```xml
   <add key="WSGI_LOG" value="C:\web\effic\logs\app.log" />
   ```
2. 打开日志文件，查看是否有Python错误信息
3. 确保logs目录存在且IIS_IUSRS用户有写入权限

### 7.3 检查事件查看器

1. 打开「事件查看器」→「Windows日志」→「应用程序」
2. 查找与IIS或Python相关的错误事件
3. 查看详细错误信息

## 8. 测试应用代码

### 8.1 验证应用可以正常运行

1. 打开命令提示符，切换到项目目录：
   ```bash
   cd C:\web\effic
   ```
2. 运行应用：
   ```bash
   python app.py
   ```
3. 检查是否有错误信息
4. 如果应用可以正常运行，说明代码本身没有问题

### 8.2 检查依赖安装

1. 确认所有依赖已正确安装：
   ```bash
   pip list
   ```
2. 确保Flask和mysql-connector-python已安装
3. 如果依赖缺失，重新安装：
   ```bash
   pip install -r requirements.txt
   ```

## 9. 检查URL重写规则

### 9.1 确认URL重写模块已安装

1. 在IIS管理器中，检查「URL重写」图标是否存在
2. 如果不存在，重新安装URL重写模块
3. 重启IIS服务

### 9.2 验证URL重写规则

1. 在IIS管理器中，选择您的站点
2. 双击「URL重写」图标
3. 确保Flask路由重写规则存在：
   - 名称：`Flask Routes`
   - 匹配URL：`.*`
   - 条件：只有当请求的文件或目录不存在时才重写
   - 操作：重写到`app.py`

## 10. 检查数据库连接

### 10.1 验证数据库服务

1. 确保MySQL服务正在运行
2. 测试数据库连接：
   ```bash
   mysql -u 用户名 -p 密码 -h 主机名 数据库名
   ```

### 10.2 检查数据库连接配置

1. 打开`app.py`文件
2. 确认数据库连接信息正确：
   ```python
   def __init__(self, host='localhost', user='用户名', password='密码', database='数据库名'):
   ```

## 11. 常见错误及解决方案

### 11.1 HTTP Error 500.19 - Internal Server Error

**错误信息**：无法访问请求的页面，因为该页的相关配置数据无效。

**解决方案**：
- 检查web.config文件是否有语法错误
- 确保安装了所有必要的IIS模块（CGI、FastCGI、URL Rewrite等）
- 检查配置节是否被锁定，运行：
  ```bash
  %windir%\system32\inetsrv\appcmd unlock config -section:system.webServer/handlers
  %windir%\system32\inetsrv\appcmd unlock config -section:system.webServer/modules
  ```

### 11.2 HTTP Error 502.2 - Bad Gateway

**错误信息**：指定的CGI应用程序遇到错误，进程被终止。

**解决方案**：
- 检查wfastcgi配置是否正确
- 确保Python路径正确
- 检查应用代码是否有错误
- 查看日志文件获取详细错误信息

### 11.3 HTTP Error 404.0 - Not Found

**错误信息**：请求的资源在服务器上未找到。

**解决方案**：
- 检查URL重写规则是否正确
- 确保app.py文件存在于站点根目录
- 检查站点物理路径配置是否正确

### 11.4 HTTP Error 403.14 - Forbidden

**错误信息**：Web服务器被配置为不列出此目录的内容。

**解决方案**：
- 确保URL重写规则存在且正确
- 检查站点默认文档配置
- 确保app.py文件存在

## 12. 高级调试

### 12.1 启用详细错误信息

1. 在IIS管理器中，选择您的站点
2. 双击「错误页」图标
3. 在右侧操作面板中，点击「编辑功能设置」
4. 选择「详细错误」
5. 点击「确定」
6. 重新访问站点，查看详细错误信息

### 12.2 测试FastCGI配置

1. 创建一个简单的Python脚本`test.py`：
   ```python
   def application(environ, start_response):
       status = '200 OK'
       headers = [('Content-type', 'text/plain; charset=utf-8')]
       start_response(status, headers)
       return [b'Hello, World!']
   ```
2. 修改web.config中的WSGI_HANDLER为`test.application`
3. 重启站点，测试访问
4. 如果可以访问，说明FastCGI配置正确，问题出在应用代码

### 12.3 检查Python版本兼容性

1. 确保Python版本与Flask版本兼容
2. 建议使用Python 3.9+版本
3. 检查Flask版本：
   ```bash
   pip show flask
   ```

## 13. 重新部署应用

如果以上步骤都无法解决问题，尝试重新部署：

1. 备份现有项目文件
2. 删除站点，重新创建
3. 重新配置web.config
4. 重新安装依赖
5. 测试访问

## 14. 联系支持

如果问题仍然无法解决，可以考虑：
- 联系您的服务器管理员
- 联系Microsoft支持（IIS相关问题）
- 检查Flask和wfastcgi的官方文档
- 在相关技术论坛寻求帮助

## 15. 附录

### 15.1 常用命令

- 重启IIS服务：
  ```bash
  iisreset
  ```

- 查看IIS站点列表：
  ```bash
  %windir%\system32\inetsrv\appcmd list site
  ```

- 查看应用池列表：
  ```bash
  %windir%\system32\inetsrv\appcmd list apppool
  ```

### 15.2 有用的资源

- [IIS官方文档](https://docs.microsoft.com/en-us/iis/)
- [wfastcgi GitHub](https://github.com/microsoft/IISFastCGI)
- [Flask官方文档](https://flask.palletsprojects.com/)
- [Microsoft IIS论坛](https://forums.iis.net/)