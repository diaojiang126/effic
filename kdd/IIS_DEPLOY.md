# IIS部署指南 - ExpressOCR 快递单信息识别系统

## 一、环境要求

### 1.1 软件要求
- Windows Server 2016/2019/2022 或 Windows 10/11 Pro
- IIS 7.5 或更高版本
- Python 3.8 或更高版本

### 1.2 Python依赖安装
```bash
pip install Flask==2.3.3 Werkzeug==2.3.7 Pillow==10.0.0 pandas==2.0.3 openpyxl==3.1.2 baidu-aip==4.16.13
```

### 1.3 wfastcgi安装
```bash
pip install wfastcgi
wfastcgi-enable
```

## 二、百度OCR配置

### 2.1 注册百度AI开放平台账号
1. 访问 https://ai.baidu.com/
2. 注册并登录百度账号
3. 进入"文字识别"服务

### 2.2 创建应用获取密钥
1. 进入"控制台" -> "文字识别"
2. 点击"创建应用"
3. 填写应用信息（应用名称、类型等）
4. 获取以下三个密钥：
   - App ID
   - API Key
   - Secret Key

### 2.3 配置密钥
修改 `config.py` 中的配置：
```python
BAIDU_AIP_APP_ID = '你的App ID'
BAIDU_AIP_API_KEY = '你的API Key'
BAIDU_AIP_SECRET_KEY = '你的Secret Key'
```

## 三、IIS配置步骤

### 3.1 启用IIS功能
1. 打开"控制面板" -> "程序和功能" -> "启用或关闭Windows功能"
2. 勾选"Internet Information Services"
3. 展开"Web管理工具" -> 勾选"IIS管理控制台"
4. 展开"万维网服务" -> 勾选"ASP.NET 4.8"（如果可用）
5. 展开"常见HTTP功能" -> 勾选"静态内容"
6. 展开"应用程序开发功能" -> 勾选"CGI"
7. 确定安装

### 3.2 创建网站
1. 打开"IIS管理器"
2. 右键"网站" -> "添加网站"
3. 配置：
   - 网站名称：`ExpressOCR`
   - 物理路径：`D:\AI\kdd`
   - 绑定类型：http
   - IP地址：全部未分配
   - 端口：5000（或其他未占用端口）

### 3.3 配置身份验证
1. 点击新创建的网站
2. 双击"身份验证"
3. 启用"匿名身份验证"
4. 如果需要，配置"Windows身份验证"

### 3.4 配置FastCGI
1. 打开"IIS管理器"
2. 点击服务器节点
3. 双击"FastCGI设置"
4. 点击"添加应用程序"
5. 配置：
   - 完整路径：`C:\Python312\python.exe`
   - 参数：`C:\Python312\Scripts\wfastcgi.py`
   - 监视对文件：`D:\AI\kdd\app.py`

### 3.5 配置环境变量
1. 在"FastCGI设置"中，双击刚添加的应用程序
2. 点击"环境变量"
3. 添加以下变量：
   - `PYTHONPATH` = `D:\AI\kdd`
   - `WSGI_HANDLER` = `app.app`
   - `PATHEXT` = `.py`

### 3.6 权限配置
1. 右键网站物理路径 -> "属性" -> "安全"
2. 添加以下用户并赋予读取/写入权限：
   - `IIS_IUSRS`
   - `IUSR`
   - `NETWORK SERVICE`

## 四、目录权限

确保以下目录有写入权限：
- `D:\AI\kdd\uploads`
- `D:\AI\kdd\exports`
- `D:\AI\kdd\express.db`（数据库文件所在目录）

## 五、防火墙配置

1. 打开"Windows防火墙" -> "高级设置"
2. 新建入站规则
3. 协议：TCP
4. 端口：5000
5. 操作：允许连接
6. 配置文件：域、专用、公用

## 六、测试验证

1. 打开浏览器访问：`http://localhost:5000`
2. 上传一张快递单图片测试
3. 检查识别结果是否正确

## 七、常见问题

### 7.1 500错误
- 检查Python依赖是否安装完整
- 检查百度OCR密钥是否正确配置
- 查看事件查看器中的应用程序日志

### 7.2 上传失败
- 检查uploads目录权限
- 检查web.config中的文件大小限制

### 7.3 OCR识别不准确
- 确保上传的快递单图片清晰
- 百度OCR免费额度有限，如需更多调用可升级付费套餐

## 八、启动和停止

- 启动网站：IIS管理器 -> 右键网站 -> 启动
- 停止网站：IIS管理器 -> 右键网站 -> 停止
- 重启网站：IIS管理器 -> 右键网站 -> 高级设置 -> 重新启动