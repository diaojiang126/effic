快递单信息识别应用 - 项目规范
====================================

一、项目概述
-----------
项目名称：ExpressOCR - 快递单信息识别系统
项目类型：Web应用程序
核心功能：上传快递单图片，自动识别收件人/发件人信息、地址、金额，保存图片，导出数据
部署方式：IIS + wfastcgi

二、技术架构
-----------
后端框架：Flask (Python 3.x)
OCR引擎：Pytesseract (开源方案) / 百度OCR API (可选)
数据库：SQLite（轻量级，无需额外配置）
前端：HTML5 + Bootstrap 5 + JavaScript
文件存储：本地文件系统

三、功能模块
-----------
1. 图片上传模块
   - 支持拖拽上传
   - 支持常见图片格式（jpg, png, jpeg, bmp）
   - 图片大小限制：10MB

2. OCR识别模块
   - 自动识别收件人姓名、电话
   - 自动识别发件人姓名、电话
   - 自动识别收件地址
   - 自动识别发件地址
   - 自动识别金额

3. 数据管理模块
   - 查看所有识别记录
   - 查看单条记录详情
   - 删除记录

4. 导出模块
   - 导出为Excel格式
   - 导出为CSV格式
   - 图片与数据关联导出

四、数据库设计
-------------
表：express_records
- id: INTEGER PRIMARY KEY
- sender_name: TEXT  # 发件人
- sender_phone: TEXT  # 发件人电话
- sender_address: TEXT  # 发件地址
- receiver_name: TEXT  # 收件人
- receiver_phone: TEXT  # 收件人电话
- receiver_address: TEXT  # 收件地址
- amount: TEXT  # 金额
- image_path: TEXT  # 图片路径
- created_at: DATETIME  # 创建时间

五、IIS部署要求
-------------
1. 安装Python运行时
2. 安装wfastcgi
3. 配置FastCGI映射
4. 设置网站物理路径
5. 配置静态文件处理

六、目录结构
-----------
express_ocr/
├── app.py              # 主应用
├── config.py           # 配置文件
├── database.py         # 数据库操作
├── ocr_engine.py       # OCR识别引擎
├── templates/
│   └── index.html      # 主页面
├── static/
│   ├── css/
│   └── js/
├── uploads/            # 上传图片存储
├── exports/             # 导出文件存储
└── requirements.txt     # 依赖列表