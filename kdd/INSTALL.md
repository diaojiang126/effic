# 快递单信息识别系统安装教程

## 系统要求

- **操作系统**：Windows 10/11 或 Linux
- **Python**：Python 3.7 或更高版本
- **依赖库**：
  - Flask 2.0+ 
  - OpenCV 4.0+ 
  - Tesseract OCR
  - SQLite3
  - NumPy
  - PIL (Pillow)
  - openpyxl
  - pandas

## 安装步骤

### 1. 安装 Python

- 访问 [Python 官网](https://www.python.org/downloads/) 下载并安装 Python 3.7 或更高版本
- 确保在安装过程中勾选 "Add Python to PATH"

### 2. 安装 Tesseract OCR

- **Windows**：
  - 下载 Tesseract OCR 安装包（推荐 v5.0.0+）
  - 安装时选择中文语言包
  - 将 Tesseract 安装目录添加到系统环境变量 PATH

- **Linux**：
  ```bash
  sudo apt-get update
  sudo apt-get install tesseract-ocr
  sudo apt-get install tesseract-ocr-chi-sim  # 安装中文语言包
  ```

### 3. 克隆项目

```bash
git clone <项目地址>
cd kdd
```

### 4. 安装依赖

```bash
pip install -r requirements.txt
```

如果没有 requirements.txt 文件，可以手动安装依赖：

```bash
pip install flask opencv-python pytesseract numpy pillow openpyxl pandas
```

### 5. 配置应用

#### 5.1 配置文件

编辑 `config.py` 文件，设置以下参数：

```python
# 应用配置
APP_NAME = "快递单信息识别系统"
DEBUG = True

# 上传配置
UPLOAD_FOLDER = "static/uploads"
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp"}

# 导出配置
EXPORT_FOLDER = r"C:\temp"  # Windows 系统
# EXPORT_FOLDER = "/tmp"  # Linux 系统

# 数据库配置
DATABASE = "express.db"

# 会话配置
SECRET_KEY = "your-secret-key-here"
```

#### 5.2 创建必要的目录

```bash
mkdir -p static/uploads
mkdir -p C:\temp  # Windows 系统
# mkdir -p /tmp  # Linux 系统
```

## 运行应用

### 1. 开发模式

```bash
python app.py
```

应用将在 `http://127.0.0.1:5010` 运行

### 2. 生产模式

#### 2.1 使用 Gunicorn (Linux)

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5010 app:app
```

#### 2.2 使用 IIS (Windows)

1. 安装 IIS 和 CGI 模块
2. 安装 wfastcgi
   ```bash
   pip install wfastcgi
   wfastcgi-enable
   ```
3. 在 IIS 中创建网站，指向项目目录
4. 配置 web.config 文件：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <handlers>
      <add name="Python FastCGI" path="*" verb="*" modules="FastCgiModule" scriptProcessor="C:\Python39\python.exe|C:\Python39\Lib\site-packages\wfastcgi.py" resourceType="Unspecified" requireAccess="Script" />
    </handlers>
    <staticContent>
      <mimeMap fileExtension=".json" mimeType="application/json" />
    </staticContent>
  </system.webServer>
  <appSettings>
    <add key="WSGI_HANDLER" value="app.app" />
    <add key="PYTHONPATH" value="D:\AI\kdd" />
  </appSettings>
</configuration>
```

## 首次使用

1. 访问应用 URL（默认为 `http://127.0.0.1:5010`）
2. 使用默认管理员账户登录：
   - 用户名：`admin`
   - 密码：`admin123`
3. 登录后，您可以：
   - 上传快递单图片进行识别
   - 管理用户（添加、删除用户）
   - 按月份清除数据
   - 导出识别记录

## 功能说明

### 1. 单文件上传
- 支持 JPG、PNG、BMP 格式的图片
- 最大支持 10MB 的图片
- 自动识别快递单上的信息

### 2. 批量上传
- 支持一次选择多张图片进行识别
- 识别完成后可通过导航查看每条记录

### 3. 识别结果编辑
- 可手动修改识别结果
- 支持添加项目和类型

### 4. 数据管理
- 查看识别记录
- 按月份清除数据
- 导出 Excel、CSV 和图片

### 5. 用户管理
- 管理员可添加和删除用户
- 支持普通用户和管理员角色

## 常见问题

### 1. 识别效果不佳
- 确保图片清晰，光线充足
- 确保快递单完整，无遮挡
- 尝试调整图片角度和大小

### 2. 上传失败
- 检查文件大小是否超过 10MB
- 检查文件格式是否为 JPG、PNG 或 BMP
- 检查服务器存储空间是否充足

### 3. 导出失败
- 检查导出目录是否存在且有写入权限
- 检查 Excel 导出时是否安装了 openpyxl

### 4. 无法登录
- 检查用户名和密码是否正确
- 检查数据库是否正常
- 尝试重启应用

### 5. 数据清除失败
- 检查是否为管理员账户
- 检查数据库连接是否正常
- 检查图片文件是否可删除

## 技术支持

如果您遇到任何问题，请联系技术支持：
- 邮箱：support@example.com
- 电话：123-456-7890

---

**注意**：本应用仅供内部使用，请勿用于商业用途。