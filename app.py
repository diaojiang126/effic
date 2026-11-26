from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_from_directory, session
import pymysql
from pymysql import Error
from datetime import datetime, date, timedelta
import json
import os
import functools
import traceback
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import uuid
import secrets
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from dotenv import load_dotenv, find_dotenv

# 加载.env文件 — 在IIS下必须指定项目根目录路径
_env_path = find_dotenv(usecwd=True)
if not _env_path:
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(_env_path, override=False)

# 快递单识别系统集成
import sys
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kdd'))

import shutil
import zipfile
from io import BytesIO
from kdd import database as express_db
from kdd.ocr_engine import parse_express_info, allowed_file as express_allowed_file
from kdd.config import UPLOAD_FOLDER as EXPRESS_UPLOAD_FOLDER, EXPORT_FOLDER as EXPRESS_EXPORT_FOLDER, DATABASE_PATH as EXPRESS_DB_PATH
from express_routes import express_bp
from auth_shared import require_auth

# 敏感字段（ITS 密码等）加解密工具
from crypto_util import (encrypt_secret, decrypt_secret, is_enabled as secret_encryption_enabled,
                         PLACEHOLDER as SECRET_PLACEHOLDER)

# 确保目录存在
os.makedirs(EXPRESS_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPRESS_EXPORT_FOLDER, exist_ok=True)

# 创建Flask应用实例
# 使用__name__参数，Flask会自动识别static目录
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.environ.get('FLASK_SECRET_KEY')
if not app.secret_key:
    print("[WARN] SECRET_KEY 未配置，Flask 会话 cookie 签名将使用弱密钥。"
          "请在 .env 或 web.config 中设置 SECRET_KEY。")
    app.secret_key = 'please-set-in-env-xxxxxx'

# JWT配置
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY') or os.environ.get('SECRET_KEY')
if not app.config['JWT_SECRET_KEY']:
    print("[WARN] JWT_SECRET_KEY 未配置，JWT token 签名将使用弱密钥。"
          "请在 .env 或 web.config 中设置 JWT_SECRET_KEY。")
    app.config['JWT_SECRET_KEY'] = 'please-set-in-env-xxxxxx'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=12)

# 数据库配置（从环境变量读取，支持.env文件和IIS web.config）
db_config = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 3306)),
    'user': os.environ.get('DB_USER', ''),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_DATABASE', 'device_inventory_system'),
}

# 启动诊断
print(f"[CONFIG] FLASK_ENV={os.environ.get('FLASK_ENV', 'not set')}")
print(f"[CONFIG] DB_HOST={db_config['host']}, DB_USER={'(set)' if db_config['user'] else '(unset)'}, DB_DATABASE={db_config['database']}")
print(f"[CONFIG] JWT密钥来源: {'ENV' if os.environ.get('JWT_SECRET_KEY') or os.environ.get('SECRET_KEY') else 'DEFAULT (请配置!)'}")

# ITS 验证码 OCR 依赖检测
try:
    from its_service import check_tesseract_available
    _ok, _msg = check_tesseract_available()
    if _ok:
        print("[ITS] Tesseract-OCR 检测通过，验证码自动识别可用")
    else:
        print(f"[ITS] 警告：{_msg}")
except Exception as _e:
    print(f"[ITS] Tesseract 检测异常: {_e}")

# 会话安全配置
app.config['SESSION_COOKIE_SECURE'] = False  # 开发环境下使用HTTP，生产环境应改为True
app.config['SESSION_COOKIE_HTTPONLY'] = True  # 防止JavaScript访问cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # 放宽Samesite策略，适应更多场景
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)  # 会话有效期8小时
app.config['SESSION_COOKIE_PATH'] = '/'  # 确保所有路径都能访问会话cookie
app.config['SESSION_COOKIE_DOMAIN'] = None  # 自动适配当前域名

# 配置模板自动重载
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # 禁用静态文件缓存

# 明确配置静态文件目录
app.static_folder = 'static'
app.static_url_path = '/static'

# 增加请求体大小限制，支持大批量数据
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# 单次批量出入库最大条数（客户端会分片提交）
MAX_BATCH_ITEMS = 200

jwt = JWTManager(app)

# 应用文件日志：IIS/wfastcgi 的 WSGI_LOG 只记录 wfastcgi 自身输出，不记录应用 print/异常，
# 导致故障（如数据库连不上）被 return [] 静默吞掉后完全无迹可查。此处单独落盘。
import logging
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
    _file_handler = RotatingFileHandler(
        os.path.join(_LOG_DIR, 'app.log'),
        maxBytes=20 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    _file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    app.logger.addHandler(_file_handler)
    app.logger.setLevel(logging.INFO)
    print(f"[LOG] 应用日志已启用: {os.path.join(_LOG_DIR, 'app.log')}")
except Exception as _log_err:
    print(f"[WARN] 应用日志初始化失败: {_log_err}")

# 权限控制装饰器
def login_required(f):
    """装饰器：限制未登录用户访问"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or request.method == 'DELETE':
                return jsonify({'success': False, 'message': '请先登录！'})
            else:
                flash('请先登录！', 'error')
                return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_company_id(for_query=False):
    """获取当前会话中的公司ID
    for_query=True: 用于查询场景，总公司可以看到所有数据
    for_query=False: 用于新增/修改场景，使用实际的company_id
    """
    company_id = session.get('company_id')
    if company_id is None and hasattr(request, 'user_info'):
        company_id = request.user_info.get('company_id')
    if company_id is None:
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            try:
                identity = json.loads(get_jwt_identity())
                company_id = identity.get('company_id')
            except:
                pass
    if for_query and company_id == 35:
        return None
    return company_id

# 批量导入页面
@app.route('/batch_import', methods=['GET'])
@login_required
def batch_import():
    """批量导入页面"""
    return render_template('batch_import.html')

# 处理批量导入请求
@app.route('/batch_import', methods=['POST'])
@login_required
def handle_batch_import():
    """处理批量导入请求"""
    try:
        import_type = request.form.get('import_type')
        file = request.files.get('file')
        
        if not import_type or not file:
            flash('请选择导入类型和文件', 'error')
            return redirect(url_for('batch_import'))
        
        # 保存上传的文件
        file_path = os.path.join('static', 'uploads', f'{uuid.uuid4()}.xlsx')
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        file.save(file_path)
        
        # 处理导入文件
        company_id = get_current_company_id()
        success = db_manager.process_import_file(file_path, import_type, company_id)
        
        # 删除临时文件
        os.remove(file_path)
        
        if success:
            flash('导入成功', 'success')
        else:
            flash('导入失败，请检查文件格式', 'error')
            
    except Exception as e:
        flash(f'导入失败: {str(e)}', 'error')
    
    return redirect(url_for('batch_import'))

# 下载导入模板
@app.route('/download_template/<template_type>')
@login_required
def download_template(template_type):
    """下载导入模板"""
    # 确保模板目录存在
    template_dir = os.path.join('static', 'templates')
    os.makedirs(template_dir, exist_ok=True)
    
    # 模板文件路径
    template_file = os.path.join(template_dir, f'{template_type}_import_template.xlsx')
    
    # 如果模板文件不存在，生成模板
    if not os.path.exists(template_file):
        # 创建Excel工作簿
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        
        if template_type == 'categories':
            # 设备分类模板
            ws.title = '设备分类'
            ws.append(['分类名称', '描述'])
            ws.append(['服务器', '用于数据处理的服务器'])
            ws.append(['网络设备', '路由器、交换机等网络设备'])
        
        elif template_type == 'models':
            # 设备型号模板
            ws.title = '设备型号'
            ws.append(['分类名称', '型号名称', '型号代码', '描述', '规格参数'])
            ws.append(['服务器', '机架式服务器', 'SRV001', '企业级机架式服务器', 'CPU: 2颗至强处理器, 内存: 128GB, 硬盘: 4TB SSD'])
        
        elif template_type == 'units':
            # 设备单元模板
            ws.title = '设备单元'
            ws.append(['型号代码', '设备编号', '序列号', '状态', '采购日期', '采购价格', '存放位置', '备注'])
            ws.append(['SRV001', 'SRV-2024-001', 'SN123456', '在库', '2024-01-01', 15000.00, '机房A', '测试服务器'])
        
        # 保存模板文件
        wb.save(template_file)
    
    # 发送模板文件
    return send_from_directory(template_dir, os.path.basename(template_file), as_attachment=True)

# 添加基本的静态文件路由，确保静态文件能够被正确访问
@app.route('/static/<path:filename>')
def serve_static(filename):
    from flask import send_from_directory
    return send_from_directory(app.static_folder, filename)

class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, host=None, user=None, password=None, database=None):
        self.host = host or db_config['host']
        self.user = user or db_config['user']
        self.password = password or db_config['password']
        self.database = database or db_config['database']
        self.connection = None
        
    def create_database(self):
        """创建数据库"""
        try:
            # 先连接到MySQL服务器，不指定数据库
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                connection_timeout=30
            )
            
            if conn.is_connected():
                cursor = conn.cursor()
                # 创建数据库
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {self.database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                cursor.close()
                conn.close()
                print(f"数据库 {self.database} 创建成功")
                return True
        except Exception as e:
            print(f"创建数据库失败: {e}")
            return False
    
    def connect(self):
        """连接到MySQL数据库

        注意：self.connection 是进程内共享的单连接。若直接覆盖赋值，旧连接（及其
        socket / 句柄）会被丢弃而永不关闭，长期运行将造成连接与句柄泄漏（本次生产
        故障即与此类资源泄漏相关）。因此这里先释放旧连接，再建立新连接。
        """
        self.close()  # 先关闭可能残留的旧连接，避免覆盖丢弃导致泄漏
        try:
            self.connection = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                connect_timeout=10,
                read_timeout=60,
                write_timeout=60,
                autocommit=False
            )
            
            print("数据库连接成功")
            return True
        except Exception as e:
            app.logger.error("连接数据库失败 host=%s err=%s: %s",
                             self.host, type(e).__name__, e, exc_info=True)
            self.connection = None
            if "1049" in str(e) or "Unknown database" in str(e):
                print("尝试创建数据库...")
                if self.create_database():
                    return self.connect()
            return False
    
    def get_connection(self):
        """获取数据库连接"""
        try:
            return pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                connect_timeout=30,
                autocommit=True
            )
        except Exception as e:
            # 记录异常类型与底层错误码：10055=套接字缓冲区/端口耗尽，1040=连接数满，
            # 2003=MySQL 未监听，1045=认证失败。缺了这行日志就无法区分根因。
            app.logger.error("获取数据库连接失败 host=%s err=%s: %s",
                             self.host, type(e).__name__, e, exc_info=True)
            return None
    
    def close(self):
        """关闭数据库连接

        必须保证 self.connection 一定被置空：若 close() 抛异常而提前返回，引用与
        底层 socket 都会残留，下一次 connect() 又会将其覆盖丢弃，形成连接泄漏。
        """
        conn = self.connection
        self.connection = None          # 先断开引用，确保无论如何都会置空
        if conn:
            try:
                conn.close()
            except Exception as e:
                print(f"关闭数据库连接失败: {e}")
    
    def create_tables(self):
        """创建数据库表"""
        if not self.connection:
            return False
            
        try:
            cursor = self.connection.cursor()
            
            # 创建设备分类表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS device_categories (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    category_name VARCHAR(100) NOT NULL,
                    description TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_category_company (category_name, company_id)
                )
            """)
            
            # 创建设备型号表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS device_models (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    category_id INT,
                    model_name VARCHAR(200) NOT NULL,
                    model_code VARCHAR(100) NOT NULL,
                    description TEXT,
                    specifications TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (category_id) REFERENCES device_categories(id),
                    UNIQUE KEY unique_model_company (model_code, company_id)
                )
            """)
            
            # 创建仓库位置表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS warehouse_locations (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    location_name VARCHAR(100) NOT NULL,
                    location_code VARCHAR(50),
                    description TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_location_company (location_name, company_id)
                )
            """)
            
            # 创建店号店名表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stores (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    store_code VARCHAR(50) NOT NULL,
                    store_name VARCHAR(200) NOT NULL,
                    contact_person VARCHAR(100),
                    phone VARCHAR(50),
                    address TEXT,
                    description TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_store_company (store_code, company_id)
                )
            """)
            
            # 创建设备编号表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS device_units (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    model_id INT,
                    unit_code VARCHAR(100) NOT NULL,
                    serial_number VARCHAR(100),
                    status VARCHAR(50) DEFAULT '在库',
                    purchase_date DATE,
                    purchase_price DECIMAL(10,2),
                    location VARCHAR(200),
                    warehouse_location_id INT,
                    store_id INT,
                    start_use_date DATE,
                    notes TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (model_id) REFERENCES device_models(id),
                    FOREIGN KEY (warehouse_location_id) REFERENCES warehouse_locations(id),
                    FOREIGN KEY (store_id) REFERENCES stores(id),
                    UNIQUE KEY unique_unit_company (unit_code, company_id)
                )
            """)
            
            # 创建入库类型表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inbound_types (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    type_name VARCHAR(100) NOT NULL,
                    description TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_inbound_type_company (type_name, company_id)
                )
            """)
            
            # 创建出库用途表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS outbound_purposes (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    purpose_name VARCHAR(100) NOT NULL,
                    description TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_purpose_company (purpose_name, company_id)
                )
            """)
            
            # 创建入库记录表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inbound_records (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    inbound_id VARCHAR(100) NOT NULL,
                    model_id INT,
                    unit_id INT,
                    quantity INT DEFAULT 1,
                    inbound_date DATETIME NOT NULL,
                    operator VARCHAR(100),
                    supplier VARCHAR(200),
                    inbound_type_id INT,
                    location VARCHAR(200),
                    warehouse_location_id INT,
                    store_id INT,
                    start_use_date DATE,
                    notes TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (model_id) REFERENCES device_models(id),
                    FOREIGN KEY (unit_id) REFERENCES device_units(id),
                    FOREIGN KEY (inbound_type_id) REFERENCES inbound_types(id),
                    FOREIGN KEY (warehouse_location_id) REFERENCES warehouse_locations(id),
                    FOREIGN KEY (store_id) REFERENCES stores(id)
                )
            """)
            
            # 创建出库记录表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS outbound_records (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    outbound_id VARCHAR(100) NOT NULL,
                    model_id INT,
                    unit_id INT,
                    quantity INT DEFAULT 1,
                    outbound_date DATETIME NOT NULL,
                    operator VARCHAR(100),
                    purpose_id INT,
                    store_name VARCHAR(200),
                    store_id INT,
                    receiver VARCHAR(100),
                    phone VARCHAR(50),
                    location VARCHAR(200),
                    warehouse_location_id INT,
                    start_use_date DATE,
                    notes TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (model_id) REFERENCES device_models(id),
                    FOREIGN KEY (unit_id) REFERENCES device_units(id),
                    FOREIGN KEY (purpose_id) REFERENCES outbound_purposes(id),
                    FOREIGN KEY (store_id) REFERENCES stores(id),
                    FOREIGN KEY (warehouse_location_id) REFERENCES warehouse_locations(id)
                )
            """)
            
            # 创建库存表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    model_id INT NOT NULL,
                    company_id INT,
                    total_quantity INT DEFAULT 0,
                    available_quantity INT DEFAULT 0,
                    last_inbound_date DATE,
                    last_outbound_date DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (model_id) REFERENCES device_models(id),
                    UNIQUE KEY unique_inventory_company (model_id, company_id)
                )
            """)
            
            # 创建操作人员表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operators (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    operator_name VARCHAR(100) NOT NULL,
                    description TEXT,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_operator_company (operator_name, company_id)
                )
            """)
            
            # 创建公司表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    company_code VARCHAR(50) UNIQUE NOT NULL,
                    company_name VARCHAR(200) NOT NULL,
                    description TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            
            # 修改用户表（添加company_id）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(100) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    company_id INT,
                    role VARCHAR(50) DEFAULT 'user',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (company_id) REFERENCES companies(id),
                    UNIQUE KEY unique_user_company (username, company_id)
                )
            """)
            
            # 创建权限表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS permissions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    permission_code VARCHAR(50) UNIQUE NOT NULL,
                    permission_name VARCHAR(100) NOT NULL,
                    description TEXT,
                    module VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 创建用户权限关联表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_permissions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    permission_id INT NOT NULL,
                    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (permission_id) REFERENCES permissions(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_user_permission (user_id, permission_id)
                )
            """)
            
            # 创建用户公司关联表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_companies (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    company_id INT NOT NULL,
                    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
                    UNIQUE KEY unique_user_company_perm (user_id, company_id)
                )
            """)
            
            # 创建区域设置表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timezone VARCHAR(100) DEFAULT 'Asia/Shanghai',
                    language VARCHAR(50) DEFAULT 'zh-CN',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            
            # 创建库存告警表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_alerts (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    model_id INT,
                    threshold INT NOT NULL,
                    alert_message VARCHAR(255) DEFAULT '库存不足',
                    is_active BOOLEAN DEFAULT TRUE,
                    company_id INT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (model_id) REFERENCES device_models(id)
                )
            """)
            
            # 为已有表添加 company_id 列（如果不存在）
            try:
                cursor.execute("ALTER TABLE stock_alerts ADD COLUMN company_id INT")
            except:
                pass
            
            # 插入默认设置（如果不存在）
            cursor.execute("SELECT COUNT(*) FROM system_settings")
            if cursor.fetchone()[0] == 0:
                cursor.execute("INSERT INTO system_settings (timezone, language) VALUES ('Asia/Shanghai', 'zh-CN')")
            
            self.connection.commit()
            cursor.close()
            return True
            
        except Exception as e:
            print(f"创建数据库表失败: {e}")
            return False
    
    def init_default_data(self):
        """初始化默认数据"""
        try:
            cursor = self.connection.cursor()
            
            # 创建默认公司（如果不存在）
            cursor.execute("SELECT id FROM companies WHERE company_code = 'HQ'")
            result = cursor.fetchone()
            if result:
                company_id = result[0]
            else:
                cursor.execute("""
                    INSERT INTO companies (company_code, company_name, description)
                    VALUES ('HQ', '总公司', '系统默认总公司')
                """)
                company_id = cursor.lastrowid
            
            # 初始化入库类型
            default_inbound_types = [
                '返修归还'
            ]
            
            for type_name in default_inbound_types:
                cursor.execute("""
                    INSERT IGNORE INTO inbound_types (type_name, company_id) VALUES (%s, %s)
                """, (type_name, company_id))
            
            # 初始化出库用途
            default_outbound_purposes = [
                '报废出库', '其他出库'
            ]
            
            for purpose_name in default_outbound_purposes:
                cursor.execute("""
                    INSERT IGNORE INTO outbound_purposes (purpose_name, company_id) VALUES (%s, %s)
                """, (purpose_name, company_id))
            
            # 仅在 admin 不存在时创建；已存在的用户绝不重置密码。
            # 否则每次服务启动都会把用户自行修改过的密码覆盖回默认值。
            from werkzeug.security import generate_password_hash
            cursor.execute("SELECT id, company_id FROM users WHERE username = 'admin'")
            admin_rows = cursor.fetchall()

            if not admin_rows:
                cursor.execute("""
                    INSERT INTO users (username, password_hash, company_id, role)
                    VALUES ('admin', %s, %s, 'admin')
                """, (generate_password_hash('admin123'), company_id))
                print("创建admin用户")
            else:
                for admin_id, admin_company_id in admin_rows:
                    if admin_company_id is None:
                        # 历史数据迁移：仅补齐公司ID，密码保持不变
                        cursor.execute("UPDATE users SET company_id = %s WHERE id = %s",
                                      (company_id, admin_id))
                        print(f"已为旧admin用户(id={admin_id})补齐公司ID（密码保持不变）")
            
            # 初始化默认权限
            default_permissions = [
                ('inventory_view', '查看库存', '查看库存列表和详情', '库存管理'),
                ('inventory_edit', '编辑库存', '入库、出库操作', '库存管理'),
                ('inbound_view', '查看出库', '查看出库记录', '入库管理'),
                ('inbound_edit', '编辑入库', '添加入库记录', '入库管理'),
                ('outbound_view', '查看出库', '查看出库记录', '出库管理'),
                ('outbound_edit', '编辑出库', '添加出库记录', '出库管理'),
                ('category_view', '查看分类', '查看设备分类', '基础数据'),
                ('category_edit', '编辑分类', '添加、编辑、删除设备分类', '基础数据'),
                ('model_view', '查看型号', '查看设备型号', '基础数据'),
                ('model_edit', '编辑型号', '添加、编辑、删除设备型号', '基础数据'),
                ('warehouse_view', '查看仓库', '查看仓库位置', '基础数据'),
                ('warehouse_edit', '编辑仓库', '添加、编辑、删除仓库位置', '基础数据'),
                ('store_view', '查看门店', '查看店号店名', '基础数据'),
                ('store_edit', '编辑门店', '添加、编辑、删除店号店名', '基础数据'),
                ('user_view', '查看用户', '查看用户列表', '系统管理'),
                ('user_edit', '编辑用户', '添加、编辑、删除用户', '系统管理'),
                ('permission_manage', '权限管理', '分配用户权限', '系统管理'),
                ('settings_view', '查看设置', '查看系统设置', '系统管理'),
                ('settings_edit', '编辑设置', '修改系统设置', '系统管理'),
                ('data_reset', '数据重置', '重置系统数据', '系统管理'),
                ('batch_import', '批量导入', '批量导入数据', '数据操作'),
                ('report_view', '查看报表', '查看统计报表', '报表'),
            ]
            
            for perm_code, perm_name, desc, module in default_permissions:
                cursor.execute("""
                    INSERT IGNORE INTO permissions (permission_code, permission_name, description, module)
                    VALUES (%s, %s, %s, %s)
                """, (perm_code, perm_name, desc, module))
            
            # 为admin用户分配所有权限
            cursor.execute("SELECT id FROM users WHERE username = 'admin'")
            admin_user = cursor.fetchone()
            if admin_user:
                admin_id = admin_user[0]
                cursor.execute("SELECT id FROM permissions")
                all_perms = cursor.fetchall()
                for perm in all_perms:
                    cursor.execute("""
                        INSERT IGNORE INTO user_permissions (user_id, permission_id)
                        VALUES (%s, %s)
                    """, (admin_id, perm[0]))
            
            # 数据库迁移：为现有表添加新字段
            migrations = [
                ("inbound_records", "warehouse_location_id", "INT"),
                ("inbound_records", "store_id", "INT"),
                ("inbound_records", "start_use_date", "DATE"),
                ("inbound_records", "serial_number", "VARCHAR(200)"),
                ("inbound_records", "unit_code", "VARCHAR(200)"),
                ("outbound_records", "store_id", "INT"),
                ("outbound_records", "warehouse_location_id", "INT"),
                ("outbound_records", "start_use_date", "DATE"),
                ("outbound_records", "serial_number", "VARCHAR(200)"),
                ("outbound_records", "unit_code", "VARCHAR(200)"),
                ("device_units", "warehouse_location_id", "INT"),
                ("device_units", "store_id", "INT"),
                ("device_units", "start_use_date", "DATE"),
                ("users", "is_active", "BOOLEAN DEFAULT TRUE"),
                ("device_categories", "company_id", "INT"),
                ("device_models", "company_id", "INT"),
                ("warehouse_locations", "company_id", "INT"),
                ("stores", "company_id", "INT"),
                ("device_units", "company_id", "INT"),
                ("inbound_types", "company_id", "INT"),
                ("outbound_purposes", "company_id", "INT"),
                ("inbound_records", "company_id", "INT"),
                ("outbound_records", "company_id", "INT"),
                ("inventory", "company_id", "INT"),
                ("operators", "company_id", "INT"),
                ("stock_alerts", "company_id", "INT"),
                ("users", "company_id", "INT"),
                # ITS 工单系统集成
                ("companies", "its_url", "VARCHAR(255)"),
                ("companies", "its_account", "VARCHAR(100)"),
                ("companies", "its_password", "VARCHAR(255)"),
                ("outbound_records", "work_order_number", "VARCHAR(100)"),
            ]
            
            for table, column, col_type in migrations:
                try:
                    cursor.execute(f"SHOW COLUMNS FROM {table} LIKE '{column}'")
                    if not cursor.fetchone():
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                        print(f"字段迁移成功: {table}.{column}")
                    else:
                        print(f"字段已存在，跳过: {table}.{column}")
                except Exception as me:
                    print(f"字段迁移警告 ({table}.{column}): {me}")
            
            # 为现有用户设置默认状态
            try:
                cursor.execute("UPDATE users SET is_active = TRUE WHERE is_active IS NULL")
                print("已为现有用户设置默认状态")
            except Exception as me:
                print(f"设置用户状态警告: {me}")
            
            # 为没有公司的用户设置默认公司
            try:
                cursor.execute("SELECT id FROM companies WHERE company_code = 'HQ'")
                hq_result = cursor.fetchone()
                if hq_result:
                    hq_id = hq_result[0]
                    cursor.execute("UPDATE users SET company_id = %s WHERE company_id IS NULL OR company_id = 0", (hq_id,))
                    print("已为没有公司的用户设置默认公司")
            except Exception as me:
                print(f"设置用户公司警告: {me}")
            
            # 数据迁移：为批量导入等历史数据中 company_id 为 NULL 的记录设置默认公司
            try:
                cursor.execute("SELECT id FROM companies WHERE company_code = 'HQ'")
                hq_result = cursor.fetchone()
                if hq_result:
                    hq_id = hq_result[0]
                    tables_to_fix = [
                        'stores', 'device_categories', 'device_models', 'device_units',
                        'warehouse_locations', 'inventory', 'inbound_records', 'outbound_records',
                        'inbound_types', 'outbound_purposes', 'operators', 'stock_alerts'
                    ]
                    for table in tables_to_fix:
                        try:
                            cursor.execute(f"UPDATE {table} SET company_id = %s WHERE company_id IS NULL OR company_id = 0", (hq_id,))
                            if cursor.rowcount > 0:
                                print(f"已修复 {table} 中 {cursor.rowcount} 条记录的 company_id")
                        except Exception as me:
                            print(f"修复 {table} 的 company_id 时警告: {me}")
            except Exception as me:
                print(f"修复历史数据 company_id 警告: {me}")

            # 分类索引修复：早期表结构对 category_name 建了单列唯一索引，导致不同公司无法各自拥有
            # 同名分类（如 711 无法新增「耗材」，因该名称已被其他公司占用）。改为 (category_name, company_id) 联合唯一。
            try:
                cursor.execute("SHOW INDEX FROM device_categories WHERE Key_name = 'category_name'")
                if cursor.fetchone():
                    cursor.execute("ALTER TABLE device_categories DROP INDEX category_name")
                    print("已删除 device_categories.category_name 单列唯一索引")
                cursor.execute("SHOW INDEX FROM device_categories WHERE Key_name = 'unique_category_company'")
                if not cursor.fetchone():
                    cursor.execute(
                        "ALTER TABLE device_categories "
                        "ADD UNIQUE KEY unique_category_company (category_name, company_id)"
                    )
                    print("已创建 device_categories 联合唯一索引 (category_name, company_id)")
            except Exception as me:
                print(f"分类索引迁移警告: {me}")

            # 分类归属修复：历史批量导入按分类名全局匹配，导致部分型号引用了其他公司的同名分类，
            # 使这些分类在型号所属公司不可见、添加型号时也无法选择。此处为型号所属公司补齐同名分类并重新指向。
            try:
                cursor.execute("""
                    SELECT DISTINCT m.company_id AS model_company, c.category_name, c.description
                    FROM device_models m
                    JOIN device_categories c ON m.category_id = c.id
                    WHERE m.company_id IS NOT NULL AND m.company_id <> 0
                      AND c.company_id IS NOT NULL AND c.company_id <> 0
                      AND c.company_id <> m.company_id
                """)
                for model_company, category_name, description in cursor.fetchall():
                    cursor.execute(
                        "SELECT id FROM device_categories WHERE category_name = %s AND company_id = %s",
                        (category_name, model_company)
                    )
                    found = cursor.fetchone()
                    if found:
                        new_category_id = found[0]
                    else:
                        cursor.execute(
                            "INSERT INTO device_categories (category_name, description, company_id) "
                            "VALUES (%s, %s, %s)",
                            (category_name, description or '', model_company)
                        )
                        new_category_id = cursor.lastrowid
                        print(f"已为公司 {model_company} 补齐分类: {category_name}")

                    cursor.execute("""
                        SELECT m.id FROM device_models m
                        JOIN device_categories c ON m.category_id = c.id
                        WHERE m.company_id = %s AND c.category_name = %s AND c.company_id <> %s
                    """, (model_company, category_name, model_company))
                    model_ids = [row[0] for row in cursor.fetchall()]
                    if model_ids:
                        placeholders = ','.join(['%s'] * len(model_ids))
                        cursor.execute(
                            f"UPDATE device_models SET category_id = %s WHERE id IN ({placeholders})",
                            (new_category_id, *model_ids)
                        )
                        print(f"已修复 {len(model_ids)} 个型号的分类归属（公司 {model_company} / {category_name}）")
            except Exception as me:
                print(f"分类归属修复警告: {me}")
            
            self.connection.commit()
            cursor.close()
            return True
            
        except Exception as e:
            print(f"初始化默认数据失败: {e}")
            return False

    def get_all_companies(self):
        """获取所有公司"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                msg = "get_all_companies: 获取数据库连接失败（数据库不可用）"
                print(msg)
                app.logger.error(msg)
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("SELECT * FROM companies WHERE is_active = TRUE ORDER BY company_name")
            companies = cursor.fetchall()
            for company in companies:
                if 'is_active' in company:
                    company['is_active'] = bool(company['is_active'])
            if not companies:
                print("get_all_companies: 查询成功但无活跃公司数据，请检查数据库初始化")
            return companies
        except Exception as e:
            print(f"获取公司列表失败: {e}")
            app.logger.exception(f"获取公司列表失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def get_company_by_id(self, company_id):
        """根据ID获取公司"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return None
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("SELECT * FROM companies WHERE id = %s", (company_id,))
            company = cursor.fetchone()
            return company
        except Exception as e:
            print(f"获取公司详情失败: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def add_company(self, company_code, company_name, description='', its_url='', its_account='', its_password=''):
        """添加公司"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()

            # ITS 密码加密入库（明文不再落库）
            its_password = encrypt_secret(its_password)

            cursor.execute("""
                INSERT INTO companies (company_code, company_name, description, its_url, its_account, its_password) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (company_code, company_name, description, its_url, its_account, its_password))
            
            
            conn.commit()
            return True
        except Exception as e:
            print(f"添加公司失败: {e}")
            
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def update_company(self, company_id, company_code, company_name, description='', its_url='', its_account='', its_password=None):
        """更新公司

        its_password 传 None 表示「不修改密码」——前端把回显的脱敏占位符（******）
        或留空提交时使用，避免把占位符写回数据库覆盖真实密码；
        传字符串时先加密再写入。
        """
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            if its_password is None:
                cursor.execute("""
                    UPDATE companies 
                    SET company_code = %s, company_name = %s, description = %s,
                        its_url = %s, its_account = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (company_code, company_name, description, its_url, its_account, company_id))
            else:
                cursor.execute("""
                    UPDATE companies 
                    SET company_code = %s, company_name = %s, description = %s,
                        its_url = %s, its_account = %s, its_password = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (company_code, company_name, description, its_url, its_account,
                      encrypt_secret(its_password), company_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"更新公司失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def delete_company(self, company_id):
        """删除公司"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False, "数据库连接失败"
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM users WHERE company_id = %s", (company_id,))
            user_count = cursor.fetchone()[0]
            
            if user_count > 0:
                return False, f"该公司下有 {user_count} 个用户，无法删除"
            
            cursor.execute("DELETE FROM companies WHERE id = %s", (company_id,))
            conn.commit()
            return True, "删除成功"
        except Exception as e:
            print(f"删除公司失败: {e}")
            return False, str(e)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def verify_user_password(self, username, password, company_id=None):
        """验证用户密码（支持公司）"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return None
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            
            # 首先验证用户名和密码
            cursor.execute("""
                SELECT u.*, c.company_name 
                FROM users u 
                LEFT JOIN companies c ON u.company_id = c.id 
                WHERE u.username = %s AND u.is_active = TRUE
            """, (username,))
            
            user = cursor.fetchone()
            if not user or not check_password_hash(user['password_hash'], password):
                return None
            
            # 如果指定了公司，检查用户是否有该公司的访问权限
            if company_id:
                try:
                    # 检查用户公司权限
                    cursor.execute("""
                        SELECT COUNT(*) FROM user_companies
                        WHERE user_id = %s AND company_id = %s
                    """, (user['id'], company_id))
                    
                    has_company_perm = cursor.fetchone()[0] > 0
                    
                    # 如果没有配置公司权限，默认允许访问所有公司
                    cursor.execute("SELECT COUNT(*) FROM user_companies WHERE user_id = %s", (user['id'],))
                    company_config_count = cursor.fetchone()[0]
                    
                    if company_config_count == 0:
                        # 没有配置公司权限，允许访问
                        pass
                    elif not has_company_perm:
                        # 配置了公司权限但不在允许列表中
                        return None
                    
                    # 获取目标公司信息
                    cursor.execute("SELECT * FROM companies WHERE id = %s", (company_id,))
                    company = cursor.fetchone()
                    if company:
                        user['company_id'] = company['id']
                        user['company_name'] = company['company_name']
                except Exception as e:
                    # 如果 user_companies 表不存在或查询失败，默认允许访问
                    print(f"公司权限检查失败（默认允许访问）: {e}")
                    cursor.execute("SELECT * FROM companies WHERE id = %s", (company_id,))
                    company = cursor.fetchone()
                    if company:
                        user['company_id'] = company['id']
                        user['company_name'] = company['company_name']
            
            return user
        except Exception as e:
            print(f"验证用户失败: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # 分类管理方法
    def get_all_categories(self, company_id=None):
        """获取所有设备分类"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("SELECT * FROM device_categories WHERE company_id = %s ORDER BY category_name", (company_id,))
            else:
                cursor.execute("SELECT * FROM device_categories ORDER BY category_name")
            categories = cursor.fetchall()
            return categories
        except Exception as e:
            print(f"获取分类失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def add_category(self, category_name, description='', company_id=None):
        """添加设备分类"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO device_categories (category_name, description, company_id) 
                VALUES (%s, %s, %s)
            """, (category_name, description, company_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"添加分类失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def delete_category(self, category_id):
        """删除设备分类"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False, "数据库连接失败"
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM device_models WHERE category_id = %s", (category_id,))
            count = cursor.fetchone()[0]
            
            if count > 0:
                return False, f"该分类下有 {count} 个设备型号，无法删除"
            
            cursor.execute("DELETE FROM device_categories WHERE id = %s", (category_id,))
            conn.commit()
            return True, "删除成功"
        except Exception as e:
            print(f"删除分类失败: {e}")
            return False, str(e)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def batch_import_categories(self, categories, company_id=None):
        """批量导入设备分类"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            
            cursor = conn.cursor()
            # 使用事务批量导入
            conn.autocommit = False
            
            for category in categories:
                cursor.execute("""
                    INSERT IGNORE INTO device_categories (category_name, description, company_id)
                    VALUES (%s, %s, %s)
                """, (category.get('category_name'), category.get('description', ''), company_id))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"批量导入分类失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # 型号管理方法
    def get_models_by_category(self, category_id):
        """获取指定分类下的所有型号"""
        try:
            cursor = self.connection.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT m.*, c.category_name 
                FROM device_models m 
                LEFT JOIN device_categories c ON m.category_id = c.id 
                WHERE m.category_id = %s 
                ORDER BY m.model_name
            """, (category_id,))
            models = cursor.fetchall()
            cursor.close()
            return models
        except Exception as e:
            print(f"获取型号失败: {e}")
            return []
    
    def get_all_models(self, company_id=None):
        """获取所有型号，包含可用库存信息"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT m.*, c.category_name, COALESCE(i.available_quantity, 0) AS available_quantity
                    FROM device_models m 
                    LEFT JOIN device_categories c ON m.category_id = c.id 
                    LEFT JOIN inventory i ON m.id = i.model_id
                    WHERE m.company_id = %s
                    ORDER BY c.category_name, m.model_name
                """, (company_id,))
            else:
                cursor.execute("""
                    SELECT m.*, c.category_name, COALESCE(i.available_quantity, 0) AS available_quantity
                    FROM device_models m 
                    LEFT JOIN device_categories c ON m.category_id = c.id 
                    LEFT JOIN inventory i ON m.id = i.model_id
                    ORDER BY c.category_name, m.model_name
                """)
            models = cursor.fetchall()
            cursor.close()
            conn.close()
            return models
        except Exception as e:
            print(f"获取所有型号失败: {e}")
            if conn:
                conn.close()
            if cursor:
                cursor.close()
            return []
    
    def get_model_by_id(self, model_id, company_id=None):
        """根据ID获取型号"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return None
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT m.*, c.category_name, COALESCE(i.available_quantity, 0) AS available_quantity
                    FROM device_models m 
                    LEFT JOIN device_categories c ON m.category_id = c.id 
                    LEFT JOIN inventory i ON m.id = i.model_id
                    WHERE m.id = %s AND m.company_id = %s
                """, (model_id, company_id))
            else:
                cursor.execute("""
                    SELECT m.*, c.category_name, COALESCE(i.available_quantity, 0) AS available_quantity
                    FROM device_models m 
                    LEFT JOIN device_categories c ON m.category_id = c.id 
                    LEFT JOIN inventory i ON m.id = i.model_id
                    WHERE m.id = %s
                """, (model_id,))
            model = cursor.fetchone()
            cursor.close()
            conn.close()
            return model
        except Exception as e:
            print(f"获取型号详情失败: {e}")
            if conn:
                conn.close()
            if cursor:
                cursor.close()
            return None
    
    def add_model(self, category_id, model_name, model_code, description='', specifications='', company_id=None):
        """添加设备型号"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            
            conn.autocommit = False
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO device_models (category_id, model_name, model_code, description, specifications, company_id) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (category_id, model_name, model_code, description, specifications, company_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"添加型号失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            if cursor:
                cursor.close()
            return False
    
    def delete_model(self, model_id):
        """删除设备型号及相关数据"""
        conn = None
        cursor = None
        try:
            # 使用独立连接，避免共享连接问题
            conn = self.get_connection()
            if not conn:
                return False
            
            # 开始事务
            conn.autocommit = False
            cursor = conn.cursor()
            
            # 1. 删除引用该型号的出库记录
            cursor.execute("DELETE FROM outbound_records WHERE model_id = %s", (model_id,))
            
            # 2. 删除引用该型号的入库记录
            cursor.execute("DELETE FROM inbound_records WHERE model_id = %s", (model_id,))
            
            # 3. 删除引用该型号的设备单元
            cursor.execute("DELETE FROM device_units WHERE model_id = %s", (model_id,))
            
            # 4. 最后删除型号本身
            cursor.execute("DELETE FROM device_models WHERE id = %s", (model_id,))
            
            # 提交事务
            conn.commit()
            print(f"删除型号成功: model_id={model_id}")
            return True
        except Exception as e:
            print(f"删除型号失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def batch_import_models(self, models, company_id=None):
        """批量导入设备型号"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            
            cursor = conn.cursor()
            # 使用事务批量导入
            conn.autocommit = False
            
            for model in models:
                # 检查分类是否存在，如果不存在则先创建（必须限定公司，避免误用其他公司的同名分类）
                cursor.execute(
                    "SELECT id FROM device_categories WHERE category_name = %s AND company_id <=> %s",
                    (model.get('category_name'), company_id)
                )
                category = cursor.fetchone()
                if category:
                    category_id = category[0]
                else:
                    # 创建新分类
                    cursor.execute("""
                        INSERT INTO device_categories (category_name, description, company_id)
                        VALUES (%s, '', %s)
                    """, (model.get('category_name'), company_id))
                    category_id = cursor.lastrowid
                
                # 插入设备型号
                cursor.execute("""
                    INSERT IGNORE INTO device_models (category_id, model_name, model_code, description, specifications, company_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (category_id, model.get('model_name'), model.get('model_code'), 
                       model.get('description', ''), model.get('specifications', ''), company_id))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"批量导入型号失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # 设备编号管理方法
    def add_device_unit(self, model_id, unit_code, serial_number='', status='在库', purchase_date=None, 
                       purchase_price=0, location='', notes='', company_id=None):
        """添加设备编号"""
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                INSERT INTO device_units (model_id, unit_code, serial_number, status, 
                purchase_date, purchase_price, location, notes, company_id) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (model_id, unit_code, serial_number, status, purchase_date, purchase_price, location, notes, company_id))
            self.connection.commit()
            cursor.close()
            return True
        except Exception as e:
            print(f"添加设备编号失败: {e}")
            return False
    
    def get_all_units(self, company_id=None):
        """获取所有设备编号"""
        try:
            cursor = self.connection.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT u.*, m.model_name, m.model_code, c.category_name 
                    FROM device_units u 
                    JOIN device_models m ON u.model_id = m.id 
                    JOIN device_categories c ON m.category_id = c.id 
                    WHERE u.company_id = %s
                    ORDER BY c.category_name, m.model_name, u.unit_code
                """, (company_id,))
            else:
                cursor.execute("""
                    SELECT u.*, m.model_name, m.model_code, c.category_name 
                    FROM device_units u 
                    JOIN device_models m ON u.model_id = m.id 
                    JOIN device_categories c ON m.category_id = c.id 
                    ORDER BY c.category_name, m.model_name, u.unit_code
                """)
            units = cursor.fetchall()
            cursor.close()
            return units
        except Exception as e:
            print(f"获取所有设备编号失败: {e}")
            return []
    
    def get_device_units_by_model(self, model_id, company_id=None):
        """获取指定型号的所有设备编号"""
        try:
            cursor = self.connection.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT u.*, m.model_name, m.model_code, c.category_name 
                    FROM device_units u 
                    JOIN device_models m ON u.model_id = m.id 
                    JOIN device_categories c ON m.category_id = c.id 
                    WHERE u.model_id = %s AND u.company_id = %s
                    ORDER BY u.unit_code
                """, (model_id, company_id))
            else:
                cursor.execute("""
                    SELECT u.*, m.model_name, m.model_code, c.category_name 
                    FROM device_units u 
                    JOIN device_models m ON u.model_id = m.id 
                    JOIN device_categories c ON m.category_id = c.id 
                    WHERE u.model_id = %s 
                    ORDER BY u.unit_code
                """, (model_id,))
            units = cursor.fetchall()
            cursor.close()
            return units
        except Exception as e:
            print(f"获取设备编号失败: {e}")
            return []
    
    def get_unit_by_id(self, unit_id, company_id=None):
        """根据ID获取设备编号"""
        try:
            cursor = self.connection.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT u.*, m.model_name, m.model_code, c.category_name 
                    FROM device_units u 
                    JOIN device_models m ON u.model_id = m.id 
                    JOIN device_categories c ON m.category_id = c.id 
                    WHERE u.id = %s AND u.company_id = %s
                """, (unit_id, company_id))
            else:
                cursor.execute("""
                    SELECT u.*, m.model_name, m.model_code, c.category_name 
                    FROM device_units u 
                    JOIN device_models m ON u.model_id = m.id 
                    JOIN device_categories c ON m.category_id = c.id 
                    WHERE u.id = %s
                """, (unit_id,))
            unit = cursor.fetchone()
            cursor.close()
            return unit
        except Exception as e:
            print(f"获取设备编号失败: {e}")
            return None
    
    def update_device_unit(self, unit_id, status='', location='', notes=''):
        """更新设备编号信息"""
        try:
            cursor = self.connection.cursor()
            updates = []
            params = []
            
            if status:
                updates.append("status = %s")
                params.append(status)
            if location:
                updates.append("location = %s")
                params.append(location)
            if notes:
                updates.append("notes = %s")
                params.append(notes)
            
            if not updates:
                cursor.close()
                return True
            
            params.append(unit_id)
            query = f"UPDATE device_units SET {', '.join(updates)} WHERE id = %s"
            
            cursor.execute(query, tuple(params))
            self.connection.commit()
            cursor.close()
            return True
        except Exception as e:
            print(f"更新设备编号失败: {e}")
            return False
    
    def delete_unit(self, unit_id):
        """删除设备编号"""
        try:
            cursor = self.connection.cursor()
            cursor.execute("DELETE FROM device_units WHERE id = %s", (unit_id,))
            self.connection.commit()
            cursor.close()
            return True
        except Exception as e:
            print(f"删除设备编号失败: {e}")
            return False
    
    def batch_import_units(self, units, company_id=None):
        """批量导入设备单元"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            
            cursor = conn.cursor()
            # 使用事务批量导入
            conn.autocommit = False
            
            for unit in units:
                # 查找设备型号ID
                cursor.execute("SELECT id FROM device_models WHERE model_code = %s", (unit.get('model_code'),))
                model = cursor.fetchone()
                if not model:
                    continue  # 跳过不存在的型号
                
                model_id = model[0]
                
                # 插入设备单元
                cursor.execute("""
                    INSERT IGNORE INTO device_units (model_id, unit_code, serial_number, status, 
                    purchase_date, purchase_price, location, notes, company_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (model_id, unit.get('unit_code'), unit.get('serial_number'), 
                       unit.get('status', '在库'), unit.get('purchase_date'), 
                       unit.get('purchase_price', 0), unit.get('location', ''), 
                       unit.get('notes', ''), company_id))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"批量导入设备单元失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    # 批量导入处理方法
    def process_import_file(self, file_path, import_type, company_id=None):
        """处理导入文件"""
        try:
            # 读取Excel文件
            df = pd.read_excel(file_path)
            
            # 根据导入类型处理数据
            if import_type == 'categories':
                # 处理设备分类数据
                categories = []
                for _, row in df.iterrows():
                    category = {
                        'category_name': str(row.get('分类名称', '')),
                        'description': str(row.get('描述', ''))
                    }
                    if category['category_name']:
                        categories.append(category)
                return self.batch_import_categories(categories, company_id)
            
            elif import_type == 'models':
                # 处理设备型号数据
                models = []
                for _, row in df.iterrows():
                    model = {
                        'category_name': str(row.get('分类名称', '')),
                        'model_name': str(row.get('型号名称', '')),
                        'model_code': str(row.get('型号代码', '')),
                        'description': str(row.get('描述', '')),
                        'specifications': str(row.get('规格参数', ''))
                    }
                    if model['model_name'] and model['model_code']:
                        models.append(model)
                return self.batch_import_models(models, company_id)
            
            elif import_type == 'units':
                # 处理设备单元数据
                units = []
                for _, row in df.iterrows():
                    unit = {
                        'model_code': str(row.get('型号代码', '')),
                        'unit_code': str(row.get('设备编号', '')),
                        'serial_number': str(row.get('序列号', '')),
                        'status': str(row.get('状态', '在库')),
                        'purchase_date': row.get('采购日期'),
                        'purchase_price': float(row.get('采购价格', 0)),
                        'location': str(row.get('存放位置', '')),
                        'notes': str(row.get('备注', ''))
                    }
                    if unit['model_code'] and unit['unit_code']:
                        units.append(unit)
                return self.batch_import_units(units, company_id)
            
            return False
        except Exception as e:
            print(f"处理导入文件失败: {e}")
            return False
    
    # 系统设置方法
    def get_system_settings(self):
        """获取系统设置"""
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("SELECT * FROM system_settings ORDER BY id LIMIT 1")
            settings = cursor.fetchone()
            cursor.close()
            conn.close()
            return settings
        except Exception as e:
            print(f"获取系统设置失败: {e}")
            return {"timezone": "Asia/Shanghai", "language": "zh-CN"}
    
    def update_system_settings(self, timezone, language):
        """更新系统设置"""
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True
            )
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE system_settings 
                SET timezone = %s, language = %s 
                ORDER BY id LIMIT 1
            """, (timezone, language))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"更新系统设置失败: {e}")
            return False
    
    def reset_all_data(self, reset_type='all', company_id=None):
        """重置数据
        
        Args:
            reset_type: 'all' - 清空所有数据
                       'inventory' - 仅清空库存和出入库记录
                       'models' - 清空设备型号和分类
            company_id: 指定公司ID，仅清除该公司的数据
        """
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=False
            )
            cursor = conn.cursor()
            
            if company_id:
                # 按公司删除数据
                if reset_type == 'all':
                    cursor.execute("DELETE FROM inventory WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM inbound_records WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM outbound_records WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM device_units WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM device_models WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM device_categories WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM warehouse_locations WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM stores WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM stock_alerts WHERE company_id = %s", (company_id,))
                    message = "该公司数据已清空"
                elif reset_type == 'inventory':
                    cursor.execute("DELETE FROM inventory WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM inbound_records WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM outbound_records WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM device_units WHERE company_id = %s", (company_id,))
                    message = "该公司库存和出入库记录已清空"
                elif reset_type == 'models':
                    cursor.execute("DELETE FROM inventory WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM inbound_records WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM outbound_records WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM device_units WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM device_models WHERE company_id = %s", (company_id,))
                    cursor.execute("DELETE FROM device_categories WHERE company_id = %s", (company_id,))
                    message = "该公司设备型号和分类已清空"
            else:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
                
                if reset_type == 'all':
                    cursor.execute("TRUNCATE TABLE inventory")
                    cursor.execute("TRUNCATE TABLE inbound_records")
                    cursor.execute("TRUNCATE TABLE outbound_records")
                    cursor.execute("TRUNCATE TABLE device_units")
                    cursor.execute("TRUNCATE TABLE device_models")
                    cursor.execute("TRUNCATE TABLE device_categories")
                    cursor.execute("TRUNCATE TABLE warehouse_locations")
                    cursor.execute("TRUNCATE TABLE stores")
                    cursor.execute("TRUNCATE TABLE stock_alerts")
                    message = "所有数据已清空"
                elif reset_type == 'inventory':
                    cursor.execute("TRUNCATE TABLE inventory")
                    cursor.execute("TRUNCATE TABLE inbound_records")
                    cursor.execute("TRUNCATE TABLE outbound_records")
                    cursor.execute("TRUNCATE TABLE device_units")
                    message = "库存和出入库记录已清空"
                elif reset_type == 'models':
                    cursor.execute("TRUNCATE TABLE inventory")
                    cursor.execute("TRUNCATE TABLE inbound_records")
                    cursor.execute("TRUNCATE TABLE outbound_records")
                    cursor.execute("TRUNCATE TABLE device_units")
                    cursor.execute("TRUNCATE TABLE device_models")
                    cursor.execute("TRUNCATE TABLE device_categories")
                    message = "设备型号和分类已清空"
                
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            
            conn.commit()
            cursor.close()
            conn.close()
            return True, message
        except Exception as e:
            print(f"重置数据失败: {e}")
            if conn:
                conn.rollback()
            if cursor:
                cursor.close()
            if conn:
                conn.close()
            return False, str(e)

    # 库存管理方法
    def get_recent_operations(self, limit=20, company_id=None):
        """获取最近的出入库操作记录"""
        # 每个方法自己管理连接，避免共享连接导致的问题
        conn = None
        cursor = None
        try:
            # 创建新连接
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            
            # 查询最近的出入库记录，合并查询
            if company_id:
                cursor.execute("""
                    SELECT 
                        '入库' as type,
                        m.model_name,
                        m.model_code,
                        m.description,
                        ir.quantity,
                        ir.inbound_date as date,
                        ir.operator,
                        ir.notes
                    FROM inbound_records ir
                    JOIN device_models m ON ir.model_id = m.id
                    WHERE ir.company_id = %s
                    
                    UNION ALL
                    
                    SELECT 
                        '出库' as type,
                        m.model_name,
                        m.model_code,
                        m.description,
                        ors.quantity,
                        ors.outbound_date as date,
                        ors.operator,
                        ors.notes
                    FROM outbound_records ors
                    JOIN device_models m ON ors.model_id = m.id
                    WHERE ors.company_id = %s
                    
                    ORDER BY date DESC
                    LIMIT %s
                """, (company_id, company_id, limit))
            else:
                cursor.execute("""
                    SELECT 
                        '入库' as type,
                        m.model_name,
                        m.model_code,
                        m.description,
                        ir.quantity,
                        ir.inbound_date as date,
                        ir.operator,
                        ir.notes
                    FROM inbound_records ir
                    JOIN device_models m ON ir.model_id = m.id
                    
                    UNION ALL
                    
                    SELECT 
                        '出库' as type,
                        m.model_name,
                        m.model_code,
                        m.description,
                        ors.quantity,
                        ors.outbound_date as date,
                        ors.operator,
                        ors.notes
                    FROM outbound_records ors
                    JOIN device_models m ON ors.model_id = m.id
                    
                    ORDER BY date DESC
                    LIMIT %s
                """, (limit,))
            
            operations = cursor.fetchall()
            return operations
        except Exception as e:
            print(f"获取最近操作记录失败: {e}")
            return []
        finally:
            # 确保资源被释放
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    

    def add_inbound_record(self, model_id, quantity, purpose_id, operator, serial_number='', unit_code='', notes='', inbound_date=None, warehouse_location_id=None, store_id=None, start_use_date=None, company_id=None):
        """添加入库记录"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                print("无法获取数据库连接")
                return False
                
            cursor = conn.cursor()
            
            print(f"添加入库记录: model_id={model_id}, quantity={quantity}, purpose_id={purpose_id}, operator={operator}")
            
            import datetime
            current_date = datetime.datetime.now().strftime('%Y%m%d')
            cursor.execute("SELECT COUNT(*) FROM inbound_records WHERE inbound_id LIKE %s AND company_id = %s", (f'{current_date}%', company_id))
            count = cursor.fetchone()[0] + 1
            inbound_id = f'{current_date}{count:04d}'
            print(f"生成入库单号: {inbound_id}")
            
            from datetime import datetime as dt
            if inbound_date:
                try:
                    inbound_date_dt = dt.strptime(inbound_date, '%Y-%m-%dT%H:%M')
                    formatted_inbound_date = inbound_date_dt.strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        inbound_date_dt = dt.strptime(inbound_date, '%Y-%m-%d')
                        formatted_inbound_date = inbound_date_dt.strftime('%Y-%m-%d 00:00:00')
                    except ValueError:
                        formatted_inbound_date = dt.now().strftime('%Y-%m-%d %H:%M:%S')
            else:
                formatted_inbound_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"处理后的入库时间: {formatted_inbound_date}")
            
            cursor.execute("""
                INSERT INTO inbound_records (inbound_id, model_id, quantity, inbound_date, operator, inbound_type_id, warehouse_location_id, store_id, start_use_date, serial_number, unit_code, notes, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (inbound_id, model_id, quantity, formatted_inbound_date, operator, purpose_id, warehouse_location_id, store_id, start_use_date, serial_number, unit_code, notes, company_id))
            print("入库记录插入成功")
            
            if serial_number and unit_code:
                cursor.execute("""
                    INSERT IGNORE INTO device_units (model_id, unit_code, serial_number, status, warehouse_location_id, store_id, start_use_date, notes)
                    VALUES (%s, %s, %s, '在库', %s, %s, %s, %s)
                """, (model_id, unit_code, serial_number, warehouse_location_id, store_id, start_use_date, notes))
                print("设备单元插入成功（如果资产编号已存在则忽略）")
            
            cursor.execute("SELECT id, total_quantity, available_quantity FROM inventory WHERE model_id = %s AND company_id = %s", (model_id, company_id))
            inventory_record = cursor.fetchone()
            
            if inventory_record:
                inventory_id = inventory_record[0]
                current_total = inventory_record[1]
                current_available = inventory_record[2]
                new_total = current_total + int(quantity)
                new_available = current_available + int(quantity)
                
                cursor.execute("""
                    UPDATE inventory 
                    SET total_quantity = %s, available_quantity = %s, last_inbound_date = %s 
                    WHERE id = %s
                """, (new_total, new_available, formatted_inbound_date, inventory_id))
                print(f"更新库存记录: inventory_id={inventory_id}, total_quantity={new_total}, available_quantity={new_available}")
            else:
                cursor.execute("""
                    INSERT INTO inventory (model_id, company_id, total_quantity, available_quantity, last_inbound_date)
                    VALUES (%s, %s, %s, %s, %s)
                """, (model_id, company_id, quantity, quantity, formatted_inbound_date))
                print(f"插入新库存记录: model_id={model_id}, company_id={company_id}, total_quantity={quantity}, available_quantity={quantity}")
            
            conn.commit()
            print("事务提交成功")
            return True
        except Exception as e:
            print(f"添加入库记录失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def add_batch_inbound_records(self, inbound_type_id, operator, items, notes='', inbound_id=None, inbound_date=None, warehouse_location_id=None, store_id=None, start_use_date=None, company_id=None):
        """批量添加入库记录，支持多选设备型号"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                print("无法获取数据库连接")
                return False
            
            cursor = conn.cursor()
            conn.autocommit = False
            
            import uuid
            
            if not inbound_id:
                inbound_id = f'IN{uuid.uuid4().hex}'
            
            from datetime import datetime
            
            if inbound_date:
                try:
                    inbound_date_dt = datetime.strptime(inbound_date, '%Y-%m-%dT%H:%M')
                    formatted_inbound_date = inbound_date_dt.strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        inbound_date_dt = datetime.strptime(inbound_date, '%Y-%m-%d')
                        formatted_inbound_date = inbound_date_dt.strftime('%Y-%m-%d 00:00:00')
                    except ValueError:
                        formatted_inbound_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            else:
                formatted_inbound_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            for item in items:
                model_id = int(item.get('model_id'))
                quantity = int(item.get('quantity', 1))
                
                print(f"准备插入入库记录: inbound_id={inbound_id}, model_id={model_id}, quantity={quantity}")
                
                sql = """
                    INSERT INTO inbound_records (inbound_id, model_id, quantity, inbound_date, operator, inbound_type_id, warehouse_location_id, store_id, start_use_date, notes, company_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                params = (inbound_id, model_id, quantity, formatted_inbound_date, operator, int(inbound_type_id), warehouse_location_id, store_id, start_use_date, notes, company_id)
                cursor.execute(sql, params)
                print("SQL执行成功")
                
                # 更新库存
                # 检查该型号是否已有库存记录
                cursor.execute("SELECT id, total_quantity, available_quantity FROM inventory WHERE model_id = %s AND company_id = %s", (model_id, company_id))
                inventory_record = cursor.fetchone()
                
                if inventory_record:
                    # 已有库存记录，更新数量
                    inventory_id = inventory_record[0]
                    current_total = inventory_record[1]
                    current_available = inventory_record[2]
                    new_total = current_total + quantity
                    new_available = current_available + quantity
                    
                    cursor.execute("""
                        UPDATE inventory 
                        SET total_quantity = %s, available_quantity = %s, last_inbound_date = %s 
                        WHERE id = %s
                    """, (new_total, new_available, formatted_inbound_date, inventory_id))
                    print(f"批量更新库存记录: inventory_id={inventory_id}, total_quantity={new_total}, available_quantity={new_available}")
                else:
                    # 没有库存记录，插入新记录
                    cursor.execute("""
                        INSERT INTO inventory (model_id, company_id, total_quantity, available_quantity, last_inbound_date)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (model_id, company_id, quantity, quantity, formatted_inbound_date))
                    print(f"批量插入新库存记录: model_id={model_id}, company_id={company_id}, total_quantity={quantity}, available_quantity={quantity}")
            
            # 提交事务
            conn.commit()
            print(f"批量入库成功! 共处理{len(items)}个物品，入库单号: {inbound_id}")
            return True
        except ValueError as e:
            print(f"批量添加入库记录参数错误: {e}")
            print(f"错误类型: {type(e).__name__}")
            # 回滚事务
            if conn:
                conn.rollback()
            return False
        except Exception as e:
            print(f"批量添加入库记录数据库错误: {e}")
            print(f"错误代码: {getattr(e, 'errno', None)}")
            print(f"错误SQL状态: {getattr(e, 'sqlstate', None)}")
            print(f"错误类型: {type(e).__name__}")
            # 回滚事务
            if conn:
                conn.rollback()
            return False
        finally:
            # 确保资源被释放
            try:
                if cursor:
                    cursor.close()
            except Exception as e:
                print(f"关闭游标失败: {e}")
            try:
                if conn:
                    conn.close()
            except Exception as e:
                print(f"关闭连接失败: {e}")

    def add_outbound_record(self, model_id, quantity, purpose_id, operator, serial_number='', unit_code='', notes='', outbound_date=None, warehouse_location_id=None, store_id=None, start_use_date=None, company_id=None, work_order_number=''):
        """添加出库记录"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                print("无法获取数据库连接")
                return False
            
            cursor = conn.cursor()
            
            import datetime
            current_date = datetime.datetime.now().strftime('%Y%m%d')
            cursor.execute("SELECT COUNT(*) FROM outbound_records WHERE outbound_id LIKE %s AND company_id = %s", (f'{current_date}%', company_id))
            count = cursor.fetchone()[0] + 1
            outbound_id = f'{current_date}{count:04d}'
            
            from datetime import datetime as dt
            if outbound_date:
                try:
                    outbound_date_dt = dt.strptime(outbound_date, '%Y-%m-%dT%H:%M')
                    formatted_outbound_date = outbound_date_dt.strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        outbound_date_dt = dt.strptime(outbound_date, '%Y-%m-%d')
                        formatted_outbound_date = outbound_date_dt.strftime('%Y-%m-%d 00:00:00')
                    except ValueError:
                        formatted_outbound_date = dt.now().strftime('%Y-%m-%d %H:%M:%S')
            else:
                formatted_outbound_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"处理后的出库时间: {formatted_outbound_date}")
            
            cursor.execute("""
                INSERT INTO outbound_records (outbound_id, model_id, quantity, outbound_date, operator, purpose_id, warehouse_location_id, store_id, start_use_date, serial_number, unit_code, notes, company_id, work_order_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (outbound_id, model_id, quantity, formatted_outbound_date, operator, purpose_id, warehouse_location_id, store_id, start_use_date, serial_number, unit_code, notes, company_id, work_order_number))
            
            if serial_number and unit_code:
                cursor.execute("""
                    UPDATE device_units 
                    SET status = '出库'
                    WHERE model_id = %s AND serial_number = %s AND unit_code = %s
                """, (model_id, serial_number, unit_code))
            
            cursor.execute("SELECT id, total_quantity, available_quantity FROM inventory WHERE model_id = %s AND company_id = %s", (model_id, company_id))
            inventory_record = cursor.fetchone()
            
            if inventory_record:
                inventory_id = inventory_record[0]
                current_total = inventory_record[1]
                current_available = inventory_record[2]
                
                if current_available < int(quantity):
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    print(f"出库失败: 可用数量不足，当前可用: {current_available}, 需求: {quantity}")
                    return False
                
                new_available = current_available - int(quantity)
                
                cursor.execute("""
                    UPDATE inventory 
                    SET available_quantity = %s, last_outbound_date = %s 
                    WHERE id = %s
                """, (new_available, formatted_outbound_date, inventory_id))
                print(f"更新出库库存记录: inventory_id={inventory_id}, available_quantity={new_available}")
            else:
                conn.rollback()
                cursor.close()
                conn.close()
                print(f"出库失败: 该型号没有库存记录")
                return False
            
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"添加出库记录失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            if cursor:
                cursor.close()
            return False
    
    def add_batch_outbound_records(self, purpose_id, operator, items, notes='', outbound_id=None, outbound_date=None, warehouse_location_id=None, store_id=None, start_use_date=None, company_id=None):
        """批量添加出库记录，支持多选设备型号"""
        conn = None
        cursor = None
        try:
            print(f"批量出库开始: purpose_id={purpose_id}, operator={operator}, items数量={len(items)}")
            
            conn = self.get_connection()
            if not conn:
                print("无法获取数据库连接")
                return False, "数据库连接失败"
            
            cursor = conn.cursor()
            conn.autocommit = False
            
            import datetime
            current_date = datetime.datetime.now().strftime('%Y%m%d')
            if not outbound_id:
                cursor.execute("SELECT COUNT(*) FROM outbound_records WHERE outbound_id LIKE %s", (f'{current_date}%',))
                count = cursor.fetchone()[0] + 1
                outbound_id = f'{current_date}{count:04d}'
            
            from datetime import datetime as dt
            if outbound_date:
                try:
                    outbound_date_dt = dt.strptime(outbound_date, '%Y-%m-%dT%H:%M')
                    formatted_outbound_date = outbound_date_dt.strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        outbound_date_dt = dt.strptime(outbound_date, '%Y-%m-%d')
                        formatted_outbound_date = outbound_date_dt.strftime('%Y-%m-%d 00:00:00')
                    except ValueError:
                        formatted_outbound_date = dt.now().strftime('%Y-%m-%d %H:%M:%S')
            else:
                formatted_outbound_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            for item in items:
                model_id = int(item.get('model_id'))
                quantity = int(item.get('quantity', 1))
                
                print(f"处理出库记录: outbound_id={outbound_id}, model_id={model_id}, quantity={quantity}")
                
                cursor.execute("""
                    INSERT INTO outbound_records (outbound_id, model_id, quantity, outbound_date, operator, purpose_id, warehouse_location_id, store_id, start_use_date, notes, company_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (outbound_id, model_id, quantity, formatted_outbound_date, operator, purpose_id, warehouse_location_id, store_id, start_use_date, notes, company_id))
                
                # 更新库存
                # 检查该型号是否已有库存记录
                cursor.execute("SELECT id, total_quantity, available_quantity FROM inventory WHERE model_id = %s AND company_id = %s", (model_id, company_id))
                inventory_record = cursor.fetchone()
                
                if inventory_record:
                    # 已有库存记录，检查可用数量是否足够
                    inventory_id = inventory_record[0]
                    current_total = inventory_record[1]
                    current_available = inventory_record[2]
                    
                    print(f"库存检查: model_id={model_id}, current_available={current_available}, quantity={quantity}, type(current_available)={type(current_available)}, type(quantity)={type(quantity)}")
                    
                    if current_available < quantity:
                        # 可用数量不足，回滚事务
                        print(f"批量出库失败: 可用数量不足，当前可用: {current_available}, 需求: {quantity}")
                        conn.rollback()
                        return False, f"型号{model_id}库存不足，当前可用: {current_available}, 需求: {quantity}"
                    
                    # 可用数量足够，更新数量
                    new_available = current_available - quantity
                    
                    cursor.execute("""
                        UPDATE inventory 
                        SET available_quantity = %s, last_outbound_date = %s 
                        WHERE id = %s
                    """, (new_available, formatted_outbound_date, inventory_id))
                    print(f"批量更新出库库存记录: inventory_id={inventory_id}, available_quantity={new_available}")
                else:
                    # 没有库存记录，无法出库
                    print(f"批量出库失败: 该型号没有库存记录")
                    conn.rollback()
                    return False, f"型号{model_id}没有库存记录"
            
            # 提交事务
            conn.commit()
            print(f"批量出库成功! 共处理{len(items)}个物品，出库单号: {outbound_id}")
            return True, f"批量出库成功，共处理{len(items)}种设备"
        except ValueError as e:
            print(f"批量添加出库记录参数错误: {e}")
            print(f"错误类型: {type(e).__name__}")
            # 回滚事务
            if conn:
                conn.rollback()
            return False, f"参数错误: {e}"
        except Exception as e:
            print(f"批量添加出库记录数据库错误: {e}")
            print(f"错误代码: {getattr(e, 'errno', None)}")
            print(f"错误SQL状态: {getattr(e, 'sqlstate', None)}")
            print(f"错误类型: {type(e).__name__}")
            # 回滚事务
            if conn:
                conn.rollback()
            return False, f"数据库错误: {e}"
        finally:
            # 确保资源被释放
            try:
                if cursor:
                    cursor.close()
            except Exception as e:
                print(f"关闭游标失败: {e}")
            try:
                if conn:
                    conn.close()
            except Exception as e:
                print(f"关闭连接失败: {e}")

    def get_inbound_records(self, company_id=None):
        """获取所有入库记录"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT i.id, i.inbound_id, i.model_id, i.unit_id, i.quantity, i.inbound_date, i.operator, i.supplier, i.inbound_type_id, i.location, i.warehouse_location_id, i.store_id, i.start_use_date, i.notes, i.created_at, i.serial_number, i.unit_code,
                           m.model_name, 
                           m.model_code, 
                           m.description, 
                           c.category_name, 
                           p.type_name as purpose_name,
                           wl.location_name as warehouse_location_name,
                           s.store_code as store_code, s.store_name as store_name,
                           DATE_FORMAT(i.inbound_date, '%%Y-%%m-%%d %%H:%%i') as formatted_inbound_date,
                           DATE_FORMAT(i.created_at, '%%Y-%%m-%%d %%H:%%i') as formatted_created_at
                    FROM inbound_records i
                    LEFT JOIN device_models m ON i.model_id = m.id
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    LEFT JOIN inbound_types p ON i.inbound_type_id = p.id
                    LEFT JOIN warehouse_locations wl ON i.warehouse_location_id = wl.id
                    LEFT JOIN stores s ON i.store_id = s.id
                    WHERE i.company_id = %s
                    ORDER BY i.inbound_date DESC
                """, (company_id,))
            else:
                cursor.execute("""
                    SELECT i.id, i.inbound_id, i.model_id, i.unit_id, i.quantity, i.inbound_date, i.operator, i.supplier, i.inbound_type_id, i.location, i.warehouse_location_id, i.store_id, i.start_use_date, i.notes, i.created_at, i.serial_number, i.unit_code,
                           m.model_name, 
                           m.model_code, 
                           m.description, 
                           c.category_name, 
                           p.type_name as purpose_name,
                           wl.location_name as warehouse_location_name,
                           s.store_code as store_code, s.store_name as store_name,
                           DATE_FORMAT(i.inbound_date, '%%Y-%%m-%%d %%H:%%i') as formatted_inbound_date,
                           DATE_FORMAT(i.created_at, '%%Y-%%m-%%d %%H:%%i') as formatted_created_at
                    FROM inbound_records i
                    LEFT JOIN device_models m ON i.model_id = m.id
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    LEFT JOIN inbound_types p ON i.inbound_type_id = p.id
                    LEFT JOIN warehouse_locations wl ON i.warehouse_location_id = wl.id
                    LEFT JOIN stores s ON i.store_id = s.id
                    ORDER BY i.inbound_date DESC
                """)
            records = cursor.fetchall()
            return records
        except Exception as e:
            print(f"获取入库记录失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def get_outbound_records(self, company_id=None):
        """获取所有出库记录"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT o.id, o.outbound_id, o.model_id, o.unit_id, o.quantity, o.outbound_date, o.operator, o.purpose_id, o.store_name as outbound_store_name, o.store_id, o.receiver, o.phone, o.location, o.warehouse_location_id, o.start_use_date, o.notes, o.created_at, o.serial_number, o.unit_code, o.work_order_number,
                           m.model_name,
                           m.model_code,
                           m.description,
                           c.category_name,
                           p.purpose_name,
                           wl.location_name as warehouse_location_name,
                           s.store_code as store_code, s.store_name as store_name,
                           DATE_FORMAT(o.outbound_date, '%%Y-%%m-%%d %%H:%%i') as formatted_outbound_date,
                           DATE_FORMAT(o.created_at, '%%Y-%%m-%%d %%H:%%i') as formatted_created_at
                    FROM outbound_records o
                    LEFT JOIN device_models m ON o.model_id = m.id
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    LEFT JOIN outbound_purposes p ON o.purpose_id = p.id
                    LEFT JOIN warehouse_locations wl ON o.warehouse_location_id = wl.id
                    LEFT JOIN stores s ON o.store_id = s.id
                    WHERE o.company_id = %s
                    ORDER BY o.outbound_date DESC
                """, (company_id,))
            else:
                cursor.execute("""
                    SELECT o.id, o.outbound_id, o.model_id, o.unit_id, o.quantity, o.outbound_date, o.operator, o.purpose_id, o.store_name as outbound_store_name, o.store_id, o.receiver, o.phone, o.location, o.warehouse_location_id, o.start_use_date, o.notes, o.created_at, o.serial_number, o.unit_code, o.work_order_number,
                           m.model_name,
                           m.model_code,
                           m.description,
                           c.category_name,
                           p.purpose_name,
                           wl.location_name as warehouse_location_name,
                           s.store_code as store_code, s.store_name as store_name,
                           DATE_FORMAT(o.outbound_date, '%%Y-%%m-%%d %%H:%%i') as formatted_outbound_date,
                           DATE_FORMAT(o.created_at, '%%Y-%%m-%%d %%H:%%i') as formatted_created_at
                    FROM outbound_records o
                    LEFT JOIN device_models m ON o.model_id = m.id
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    LEFT JOIN outbound_purposes p ON o.purpose_id = p.id
                    LEFT JOIN warehouse_locations wl ON o.warehouse_location_id = wl.id
                    LEFT JOIN stores s ON o.store_id = s.id
                    ORDER BY o.outbound_date DESC
                """)
            records = cursor.fetchall()
            return records
        except Exception as e:
            print(f"获取出库记录失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def get_inbound_purposes(self, company_id=None):
        """获取入库用途列表"""
        # 每个方法自己管理连接，避免共享连接导致的问题
        conn = None
        cursor = None
        try:
            # 创建新连接
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("SELECT id, type_name FROM inbound_types WHERE company_id = %s ORDER BY type_name", (company_id,))
            else:
                cursor.execute("SELECT id, type_name FROM inbound_types ORDER BY type_name")
            purposes = cursor.fetchall()
            # 统一返回字段名，与出库用途保持一致
            for purpose in purposes:
                purpose['purpose_name'] = purpose['type_name']
            return purposes
        except Exception as e:
            print(f"获取入库用途失败: {e}")
            return []
        finally:
            # 确保资源被释放
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def add_inbound_purpose(self, purpose_name, company_id=None):
        """添加入库用途"""
        try:
            # 假设调用者已经确保了连接有效
            cursor = self.connection.cursor()
            cursor.execute("""
                INSERT INTO inbound_types (type_name, company_id)
                VALUES (%s, %s)
            """, (purpose_name, company_id))
            self.connection.commit()
            cursor.close()
            return True
        except Exception as e:
            print(f"添加入库用途失败: {e}")
            return False
    
    def add_outbound_purpose(self, purpose_name, company_id=None):
        """添加出库用途"""
        try:
            # 假设调用者已经确保了连接有效
            cursor = self.connection.cursor()
            cursor.execute("""
                INSERT INTO outbound_purposes (purpose_name, company_id)
                VALUES (%s, %s)
            """, (purpose_name, company_id))
            self.connection.commit()
            cursor.close()
            return True
        except Exception as e:
            print(f"添加出库用途失败: {e}")
            return False
    
    def delete_outbound_purpose(self, purpose_id):
        """删除出库用途"""
        # 每个方法自己管理连接，避免共享连接导致的问题
        conn = None
        cursor = None
        try:
            # 创建新连接
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True
            )
            cursor = conn.cursor()
            
            # 检查是否有出库记录引用该用途
            cursor.execute("SELECT COUNT(*) FROM outbound_records WHERE purpose_id = %s", (purpose_id,))
            count = cursor.fetchone()[0]
            if count > 0:
                print(f"无法删除出库用途，有{count}条记录引用该用途")
                return (False, f"该出库用途正在被{count}条记录使用，无法删除")
            
            # 执行删除操作
            cursor.execute("DELETE FROM outbound_purposes WHERE id = %s", (purpose_id,))
            conn.commit()
            return (True, "出库用途删除成功")
        except Exception as e:
            print(f"删除出库用途失败: {e}")
            return (False, f"删除失败: {str(e)}")
        finally:
            # 确保资源被释放
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def delete_inbound_purpose(self, purpose_id):
        """删除入库用途"""
        # 每个方法自己管理连接，避免共享连接导致的问题
        conn = None
        cursor = None
        try:
            # 创建新连接
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True
            )
            cursor = conn.cursor()
            
            # 检查是否有入库记录引用该用途
            cursor.execute("SELECT COUNT(*) FROM inbound_records WHERE inbound_type_id = %s", (purpose_id,))
            count = cursor.fetchone()[0]
            if count > 0:
                print(f"无法删除入库用途，有{count}条记录引用该用途")
                return (False, f"该入库用途正在被{count}条记录使用，无法删除")
            
            # 执行删除操作
            cursor.execute("DELETE FROM inbound_types WHERE id = %s", (purpose_id,))
            conn.commit()
            return (True, "入库用途删除成功")
        except Exception as e:
            print(f"删除入库用途失败: {e}")
            return (False, f"删除失败: {str(e)}")
        finally:
            # 确保资源被释放
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def get_outbound_purposes(self, company_id=None):
        """获取出库用途列表"""
        # 每个方法自己管理连接，避免共享连接导致的问题
        conn = None
        cursor = None
        try:
            # 创建新连接
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("SELECT id, purpose_name FROM outbound_purposes WHERE company_id = %s ORDER BY purpose_name", (company_id,))
            else:
                cursor.execute("SELECT id, purpose_name FROM outbound_purposes ORDER BY purpose_name")
            purposes = cursor.fetchall()
            return purposes
        except Exception as e:
            print(f"获取出库用途失败: {e}")
            return []
        finally:
            # 确保资源被释放
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    # 仓库位置管理方法
    def get_all_warehouse_locations(self, company_id=None):
        """获取所有仓库位置"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("SELECT * FROM warehouse_locations WHERE company_id = %s ORDER BY location_name", (company_id,))
            else:
                cursor.execute("SELECT * FROM warehouse_locations ORDER BY location_name")
            locations = cursor.fetchall()
            return locations
        except Exception as e:
            print(f"获取仓库位置失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def add_warehouse_location(self, location_name, location_code='', description='', company_id=None):
        """添加仓库位置"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO warehouse_locations (location_name, location_code, description, company_id)
                VALUES (%s, %s, %s, %s)
            """, (location_name, location_code, description, company_id))
            return True
        except Exception as e:
            print(f"添加仓库位置失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def update_warehouse_location(self, location_id, location_name, location_code='', description=''):
        """更新仓库位置"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE warehouse_locations 
                SET location_name = %s, location_code = %s, description = %s
                WHERE id = %s
            """, (location_name, location_code, description, location_id))
            return True
        except Exception as e:
            print(f"更新仓库位置失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def delete_warehouse_location(self, location_id):
        """删除仓库位置"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            cursor = conn.cursor()
            cursor.execute("DELETE FROM warehouse_locations WHERE id = %s", (location_id,))
            return True
        except Exception as e:
            print(f"删除仓库位置失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    # 店号店名管理方法
    def get_all_stores(self, company_id=None):
        """获取所有店号店名"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("SELECT * FROM stores WHERE company_id = %s ORDER BY store_code", (company_id,))
            else:
                cursor.execute("SELECT * FROM stores ORDER BY store_code")
            stores = cursor.fetchall()
            return stores
        except Exception as e:
            print(f"获取店号店名失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def add_store(self, store_code, store_name, contact_person='', phone='', address='', description='', company_id=None):
        """添加店号店名"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stores (store_code, store_name, contact_person, phone, address, description, company_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (store_code, store_name, contact_person, phone, address, description, company_id))
            return True
        except Exception as e:
            print(f"添加店号店名失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def update_store(self, store_id, store_code, store_name, contact_person='', phone='', address='', description=''):
        """更新店号店名"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE stores 
                SET store_code = %s, store_name = %s, contact_person = %s, phone = %s, address = %s, description = %s
                WHERE id = %s
            """, (store_code, store_name, contact_person, phone, address, description, store_id))
            return True
        except Exception as e:
            print(f"更新店号店名失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def delete_store(self, store_id):
        """删除店号店名"""
        conn = None
        cursor = None
        try:
            print(f"开始删除店号店名，ID: {store_id}")
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=True
            )
            print("数据库连接成功")
            cursor = conn.cursor()
            
            # 检查是否有设备单元引用该店号店名
            print("检查设备单元引用...")
            cursor.execute("SELECT COUNT(*) FROM device_units WHERE store_id = %s", (store_id,))
            device_units_count = cursor.fetchone()[0]
            print(f"设备单元引用数量: {device_units_count}")
            
            # 检查是否有入库记录引用该店号店名
            print("检查入库记录引用...")
            cursor.execute("SELECT COUNT(*) FROM inbound_records WHERE store_id = %s", (store_id,))
            inbound_records_count = cursor.fetchone()[0]
            print(f"入库记录引用数量: {inbound_records_count}")
            
            # 检查是否有出库记录引用该店号店名
            print("检查出库记录引用...")
            cursor.execute("SELECT COUNT(*) FROM outbound_records WHERE store_id = %s", (store_id,))
            outbound_records_count = cursor.fetchone()[0]
            print(f"出库记录引用数量: {outbound_records_count}")
            
            total_count = device_units_count + inbound_records_count + outbound_records_count
            print(f"总引用数量: {total_count}")
            
            if total_count > 0:
                error_messages = []
                if device_units_count > 0:
                    error_messages.append(f"{device_units_count}个设备单元")
                if inbound_records_count > 0:
                    error_messages.append(f"{inbound_records_count}条入库记录")
                if outbound_records_count > 0:
                    error_messages.append(f"{outbound_records_count}条出库记录")
                
                error_msg = f"该店号店名正在被{'、'.join(error_messages)}使用，无法删除"
                print(f"无法删除店号店名: {error_msg}")
                return (False, error_msg)
            
            # 执行删除操作
            print("执行删除操作...")
            cursor.execute("DELETE FROM stores WHERE id = %s", (store_id,))
            affected_rows = cursor.rowcount
            print(f"删除操作影响的行数: {affected_rows}")
            
            if affected_rows > 0:
                return (True, "店号店名删除成功")
            else:
                return (False, "店号店名不存在")
        except pymysql.err.IntegrityError as e:
            print(f"删除店号店名失败（外键约束）: {e}")
            import traceback
            traceback.print_exc()
            # 检查错误信息中是否包含外键约束
            error_msg = str(e).lower()
            if 'foreign key constraint' in error_msg:
                return (False, "该店号店名正在被其他数据引用，无法删除。请先清除相关记录。")
            else:
                return (False, f"删除失败（数据库约束）: {str(e)}")
        except Exception as e:
            print(f"删除店号店名失败: {e}")
            import traceback
            traceback.print_exc()
            return (False, f"删除失败: {str(e)}")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
            print("数据库连接已关闭")
    
    def batch_import_stores(self, stores_data, company_id=None):
        """批量导入店号店名"""
        conn = None
        cursor = None
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                autocommit=False
            )
            cursor = conn.cursor()
            success_count = 0
            error_count = 0
            
            for store in stores_data:
                try:
                    cursor.execute("""
                        INSERT INTO stores (store_code, store_name, contact_person, phone, address, description, company_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE 
                        store_name = VALUES(store_name),
                        contact_person = VALUES(contact_person),
                        phone = VALUES(phone),
                        address = VALUES(address),
                        description = VALUES(description)
                    """, (store.get('store_code'), store.get('store_name'), 
                          store.get('contact_person', ''), store.get('phone', ''),
                          store.get('address', ''), store.get('description', ''), company_id))
                    success_count += 1
                except Exception as e:
                    print(f"导入店号店名失败: {store.get('store_code')}, 错误: {e}")
                    error_count += 1
            
            conn.commit()
            return True, f"成功导入 {success_count} 条，失败 {error_count} 条"
        except Exception as e:
            print(f"批量导入店号店名失败: {e}")
            if conn:
                conn.rollback()
            return False, str(e)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    # 操作人员管理方法
    def get_all_operators(self, company_id=None):
        """获取所有操作人员"""
        try:
            cursor = self.connection.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("SELECT * FROM operators WHERE company_id = %s ORDER BY operator_name", (company_id,))
            else:
                cursor.execute("SELECT * FROM operators ORDER BY operator_name")
            operators = cursor.fetchall()
            cursor.close()
            return operators
        except Exception as e:
            print(f"获取操作人员失败: {e}")
            return []
    
    def add_operator(self, operator_name, description='', company_id=None):
        """添加操作人员"""
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                INSERT INTO operators (operator_name, description, company_id)
                VALUES (%s, %s, %s)
            """, (operator_name, description, company_id))
            self.connection.commit()
            cursor.close()
            return True
        except Exception as e:
            print(f"添加操作人员失败: {e}")
            return False
    
    def delete_operator(self, operator_id):
        """删除操作人员"""
        try:
            cursor = self.connection.cursor()
            cursor.execute("DELETE FROM operators WHERE id = %s", (operator_id,))
            self.connection.commit()
            cursor.close()
            return True
        except Exception as e:
            print(f"删除操作人员失败: {e}")
            return False
    
    # 用户管理方法
    def add_user(self, username, password, role='user'):
        """添加用户"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
                
            cursor = conn.cursor()
            # 检查用户名是否已存在
            cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cursor.fetchone():
                # 用户名已存在
                cursor.close()
                conn.close()
                return False
                
            # 生成密码哈希
            password_hash = generate_password_hash(password)
            cursor.execute("""
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
            """, (username, password_hash, role))
            conn.commit()
            return True
        except Exception as e:
            print(f"添加用户失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def get_user_by_username(self, username):
        """通过用户名获取用户信息"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return None
                
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            # 确保使用正确的用户名查询，不区分大小写？
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            print(f"获取用户信息: username={username}, user={user}")  # 添加调试日志
            return user
        except Exception as e:
            print(f"获取用户失败: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    # verify_user_password 已移到上方（支持 company_id）
    
    def get_all_users(self):
        """获取所有用户信息"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
                
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT * FROM users 
                ORDER BY created_at DESC
            """)
            users = cursor.fetchall()
            return users
        except Exception as e:
            print(f"获取用户列表失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def change_user_password(self, user_id, password):
        """修改用户密码"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
                
            cursor = conn.cursor()
            # 生成新的密码哈希
            password_hash = generate_password_hash(password)
            cursor.execute("""
                UPDATE users 
                SET password_hash = %s, updated_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (password_hash, user_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            print(f"修改用户密码失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def delete_user(self, user_id):
        """删除用户"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
                
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"删除用户失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def toggle_user_status(self, user_id):
        """切换用户状态（启用/禁用）"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            cursor.execute("SELECT is_active FROM users WHERE id = %s", (user_id,))
            result = cursor.fetchone()
            if result:
                new_status = not result[0]
                cursor.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_status, user_id))
                conn.commit()
                return True, new_status
            return False, None
        except Exception as e:
            print(f"切换用户状态失败: {e}")
            if conn:
                conn.rollback()
            return False, None
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    # 权限管理方法
    def get_all_permissions(self):
        """获取所有权限"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("SELECT * FROM permissions ORDER BY module, permission_name")
            return cursor.fetchall()
        except Exception as e:
            print(f"获取权限列表失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def get_user_permissions(self, user_id):
        """获取用户权限"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT p.* FROM permissions p
                JOIN user_permissions up ON p.id = up.permission_id
                WHERE up.user_id = %s
            """, (user_id,))
            return cursor.fetchall()
        except Exception as e:
            print(f"获取用户权限失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def update_user_permissions(self, user_id, permission_ids):
        """更新用户权限"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM user_permissions WHERE user_id = %s", (user_id,))
            
            for perm_id in permission_ids:
                cursor.execute("""
                    INSERT INTO user_permissions (user_id, permission_id)
                    VALUES (%s, %s)
                """, (user_id, perm_id))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"更新用户权限失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def user_has_permission(self, user_id, permission_code):
        """检查用户是否有指定权限"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT COUNT(*) FROM user_permissions up
                JOIN permissions p ON up.permission_id = p.id
                WHERE up.user_id = %s AND p.permission_code = %s
            """, (user_id, permission_code))
            
            count = cursor.fetchone()[0]
            return count > 0
        except Exception as e:
            print(f"检查用户权限失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def get_user_companies(self, user_id):
        """获取用户可访问的公司列表"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT c.* FROM companies c
                JOIN user_companies uc ON c.id = uc.company_id
                WHERE uc.user_id = %s
                ORDER BY c.company_name
            """, (user_id,))
            return cursor.fetchall()
        except Exception as e:
            print(f"获取用户公司权限失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def update_user_companies(self, user_id, company_ids):
        """更新用户可访问的公司列表"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM user_companies WHERE user_id = %s", (user_id,))
            
            for comp_id in company_ids:
                cursor.execute("""
                    INSERT INTO user_companies (user_id, company_id)
                    VALUES (%s, %s)
                """, (user_id, comp_id))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"更新用户公司权限失败: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def user_has_company_access(self, user_id, company_id):
        """检查用户是否有指定公司的访问权限"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            
            # 检查用户是否有任何公司权限配置
            cursor.execute("SELECT COUNT(*) FROM user_companies WHERE user_id = %s", (user_id,))
            config_count = cursor.fetchone()[0]
            
            # 如果没有配置任何公司权限，默认允许访问所有公司
            if config_count == 0:
                return True
            
            # 否则检查是否有指定公司的权限
            cursor.execute("""
                SELECT COUNT(*) FROM user_companies
                WHERE user_id = %s AND company_id = %s
            """, (user_id, company_id))
            
            count = cursor.fetchone()[0]
            return count > 0
        except Exception as e:
            print(f"检查用户公司权限失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # 库存管理方法
    def get_inventory(self, company_id=None):
        """获取所有库存信息"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT i.*, m.model_name, m.model_code, m.description, c.category_name
                    FROM inventory i
                    JOIN device_models m ON i.model_id = m.id
                    JOIN device_categories c ON m.category_id = c.id
                    WHERE i.company_id = %s
                    ORDER BY c.category_name, m.model_name
                """, (company_id,))
            else:
                cursor.execute("""
                    SELECT i.*, m.model_name, m.model_code, m.description, c.category_name
                    FROM inventory i
                    JOIN device_models m ON i.model_id = m.id
                    JOIN device_categories c ON m.category_id = c.id
                    ORDER BY c.category_name, m.model_name
                """)
            inventory = cursor.fetchall()
            cursor.close()
            conn.close()
            return inventory
        except Exception as e:
            print(f"获取库存信息失败: {e}")
            if conn:
                conn.close()
            if cursor:
                cursor.close()
            return []

    def get_model_stock(self, company_id=None):
        """获取各型号当前库存（含从未入库、无库存记录的型号，数量按 0 返回），供出库界面实时展示可用库存"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT m.id AS model_id, m.model_name, m.model_code,
                           COALESCE(c.category_name, '未分类') AS category_name,
                           COALESCE(SUM(i.total_quantity), 0) AS total_quantity,
                           COALESCE(SUM(i.available_quantity), 0) AS available_quantity
                    FROM device_models m
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    LEFT JOIN inventory i ON i.model_id = m.id AND i.company_id = %s
                    WHERE m.company_id = %s
                    GROUP BY m.id, m.model_name, m.model_code, c.category_name
                    ORDER BY c.category_name, m.model_name
                """, (company_id, company_id))
            else:
                cursor.execute("""
                    SELECT m.id AS model_id, m.model_name, m.model_code,
                           COALESCE(c.category_name, '未分类') AS category_name,
                           COALESCE(SUM(i.total_quantity), 0) AS total_quantity,
                           COALESCE(SUM(i.available_quantity), 0) AS available_quantity
                    FROM device_models m
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    LEFT JOIN inventory i ON i.model_id = m.id
                    GROUP BY m.id, m.model_name, m.model_code, c.category_name
                    ORDER BY c.category_name, m.model_name
                """)
            # SUM() 返回 Decimal，显式转为 int，保证接口返回 JSON 数字而不是字符串
            return [
                {
                    'model_id': int(row.get('model_id') or 0),
                    'model_name': row.get('model_name') or '',
                    'model_code': row.get('model_code') or '',
                    'category_name': row.get('category_name') or '未分类',
                    'total_quantity': int(row.get('total_quantity') or 0),
                    'available_quantity': int(row.get('available_quantity') or 0)
                }
                for row in cursor.fetchall()
            ]
        except Exception as e:
            print(f"获取型号库存失败: {e}")
            traceback.print_exc()
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def get_inventory_by_id(self, inventory_id):
        """根据ID获取库存信息"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return None
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT i.*, m.model_name, m.model_code, c.category_name
                FROM inventory i
                JOIN device_models m ON i.model_id = m.id
                JOIN device_categories c ON m.category_id = c.id
                WHERE i.id = %s
            """, (inventory_id,))
            inventory = cursor.fetchone()
            cursor.close()
            conn.close()
            return inventory
        except Exception as e:
            print(f"获取库存信息失败: {e}")
            if conn:
                conn.close()
            if cursor:
                cursor.close()
            return None
    
    def update_inventory(self, inventory_id, total_quantity=None, available_quantity=None):
        """更新库存信息"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            updates = []
            params = []
            
            if total_quantity is not None:
                updates.append("total_quantity = %s")
                params.append(total_quantity)
            if available_quantity is not None:
                updates.append("available_quantity = %s")
                params.append(available_quantity)
            
            if not updates:
                cursor.close()
                conn.close()
                return True
            
            params.append(inventory_id)
            query = f"UPDATE inventory SET {', '.join(updates)} WHERE id = %s"
            
            cursor.execute(query, tuple(params))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"更新库存失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            if cursor:
                cursor.close()
            return False
    
    def delete_inventory(self, inventory_id):
        """删除库存记录"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            cursor.execute("DELETE FROM inventory WHERE id = %s", (inventory_id,))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"删除库存失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            if cursor:
                cursor.close()
            return False
    
    def add_inventory(self, model_id, total_quantity, available_quantity, company_id=None):
        """添加库存记录"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO inventory (model_id, company_id, total_quantity, available_quantity)
                VALUES (%s, %s, %s, %s)
            """, (model_id, company_id, total_quantity, available_quantity))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"添加库存失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            if cursor:
                cursor.close()
            return False
    
    # 库存告警管理方法
    def get_stock_alerts(self, company_id=None):
        """获取所有库存告警设置"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT sa.*, m.model_name, m.model_code, c.category_name
                    FROM stock_alerts sa
                    JOIN device_models m ON sa.model_id = m.id
                    JOIN device_categories c ON m.category_id = c.id
                    WHERE sa.company_id = %s
                    ORDER BY c.category_name, m.model_name
                """, (company_id,))
            else:
                cursor.execute("""
                    SELECT sa.*, m.model_name, m.model_code, c.category_name
                    FROM stock_alerts sa
                    JOIN device_models m ON sa.model_id = m.id
                    JOIN device_categories c ON m.category_id = c.id
                    ORDER BY c.category_name, m.model_name
                """)
            alerts = cursor.fetchall()
            cursor.close()
            conn.close()
            return alerts
        except Exception as e:
            print(f"获取库存告警设置失败: {e}")
            if conn:
                conn.close()
            if cursor:
                cursor.close()
            return []
    
    def get_stock_alert_by_id(self, alert_id):
        """根据ID获取库存告警设置"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return None
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT sa.*, m.model_name, m.model_code, c.category_name
                FROM stock_alerts sa
                JOIN device_models m ON sa.model_id = m.id
                JOIN device_categories c ON m.category_id = c.id
                WHERE sa.id = %s
            """, (alert_id,))
            alert = cursor.fetchone()
            cursor.close()
            conn.close()
            return alert
        except Exception as e:
            print(f"获取库存告警设置失败: {e}")
            if conn:
                conn.close()
            if cursor:
                cursor.close()
            return None
    
    def add_stock_alert(self, model_id, threshold, alert_message, is_active=True, company_id=None):
        """添加库存告警设置"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stock_alerts (model_id, threshold, alert_message, is_active, company_id)
                VALUES (%s, %s, %s, %s, %s)
            """, (model_id, threshold, alert_message, is_active, company_id))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"添加库存告警设置失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            if cursor:
                cursor.close()
            return False
    
    def update_stock_alert(self, alert_id, threshold=None, alert_message=None, is_active=None):
        """更新库存告警设置"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            updates = []
            params = []
            
            if threshold is not None:
                updates.append("threshold = %s")
                params.append(threshold)
            if alert_message is not None:
                updates.append("alert_message = %s")
                params.append(alert_message)
            if is_active is not None:
                updates.append("is_active = %s")
                params.append(is_active)
            
            if not updates:
                cursor.close()
                conn.close()
                return True
            
            params.append(alert_id)
            query = f"UPDATE stock_alerts SET {', '.join(updates)} WHERE id = %s"
            
            cursor.execute(query, tuple(params))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"更新库存告警设置失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            if cursor:
                cursor.close()
            return False
    
    def delete_stock_alert(self, alert_id):
        """删除库存告警设置"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stock_alerts WHERE id = %s", (alert_id,))
            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            print(f"删除库存告警设置失败: {e}")
            if conn:
                conn.rollback()
                conn.close()
            if cursor:
                cursor.close()
            return False
    
    def check_stock_alerts(self):
        """检查库存告警，返回需要告警的设备列表"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT sa.*, m.model_name, m.model_code, m.description, c.category_name, i.available_quantity 
                FROM stock_alerts sa
                JOIN device_models m ON sa.model_id = m.id
                JOIN device_categories c ON m.category_id = c.id
                JOIN inventory i ON sa.model_id = i.model_id
                WHERE sa.is_active = TRUE
            """)
            alerts = cursor.fetchall()
            cursor.close()
            conn.close()
            
            # 手动过滤，确保类型正确
            filtered_alerts = []
            for alert in alerts:
                try:
                    available_quantity = int(alert['available_quantity'])
                    threshold = int(alert['threshold'])
                    if available_quantity <= threshold:
                        filtered_alerts.append(alert)
                except (ValueError, TypeError) as e:
                    print(f"告警检查类型转换错误: {e}, 告警ID: {alert['id']}")
                    continue
            
            return filtered_alerts
        except Exception as e:
            print(f"检查库存告警失败: {e}")
            if conn:
                conn.close()
            if cursor:
                cursor.close()
            return []
    
    # 统计方法
    def get_statistics(self, company_id=None):
        """获取统计数据"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return {}
            
            cursor = conn.cursor()
            
            if company_id:
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM inbound_records WHERE company_id = %s", (company_id,))
                inbound_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM outbound_records WHERE company_id = %s", (company_id,))
                outbound_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM device_models WHERE company_id = %s", (company_id,))
                total_models = cursor.fetchone()[0]

                cursor.execute("SELECT COALESCE(SUM(total_quantity), 0) FROM inventory WHERE company_id = %s", (company_id,))
                total_quantity = cursor.fetchone()[0]

                cursor.execute("SELECT COALESCE(SUM(available_quantity), 0) FROM inventory WHERE company_id = %s", (company_id,))
                total_available = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM device_categories WHERE company_id = %s", (company_id,))
                category_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM stores WHERE company_id = %s", (company_id,))
                store_count = cursor.fetchone()[0]
            else:
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM inbound_records")
                inbound_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM outbound_records")
                outbound_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM device_models")
                total_models = cursor.fetchone()[0]

                cursor.execute("SELECT COALESCE(SUM(total_quantity), 0) FROM inventory")
                total_quantity = cursor.fetchone()[0]

                cursor.execute("SELECT COALESCE(SUM(available_quantity), 0) FROM inventory")
                total_available = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM device_categories")
                category_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM stores")
                store_count = cursor.fetchone()[0]
            
            return {
                'total_models': total_models,
                'total_quantity': total_quantity,
                'total_available': total_available,
                'inbound_count': inbound_count,
                'outbound_count': outbound_count,
                'category_count': category_count,
                'store_count': store_count,
                'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        except Exception as e:
            print(f"获取统计数据失败: {e}")
            return {}
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    # 库存统计方法
    def get_inventory_stats(self, company_id=None):
        """获取库存统计数据（按分类）"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT c.category_name, COALESCE(SUM(i.available_quantity), 0) as total_quantity
                    FROM device_categories c
                    LEFT JOIN device_models m ON c.id = m.category_id AND m.company_id = %s
                    LEFT JOIN inventory i ON m.id = i.model_id AND i.company_id = %s
                    WHERE c.company_id = %s
                    GROUP BY c.id, c.category_name
                    ORDER BY c.category_name
                """, (company_id, company_id, company_id))
            else:
                cursor.execute("""
                    SELECT c.category_name, COALESCE(SUM(i.available_quantity), 0) as total_quantity
                    FROM device_categories c
                    LEFT JOIN device_models m ON c.id = m.category_id
                    LEFT JOIN inventory i ON m.id = i.model_id
                    GROUP BY c.id, c.category_name
                    ORDER BY c.category_name
                """)
            stats = cursor.fetchall()
            return stats
        except Exception as e:
            print(f"获取库存统计数据失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def get_report_summary(self, company_id=None, days=7):
        """报表汇总数据：今日出入库、近N天趋势、今日各品类出入库明细、库存概况"""
        conn = None
        cursor = None
        try:
            try:
                days = int(days)
            except (TypeError, ValueError):
                days = 7
            days = max(3, min(days, 30))

            conn = self.get_connection()
            if not conn:
                print("获取报表数据失败: 数据库连接失败")
                return None

            cursor = conn.cursor(pymysql.cursors.DictCursor)
            company_filter = " AND company_id = %s" if company_id else ""
            company_params = (company_id,) if company_id else ()
            today_start = "CURDATE()"
            today_end = "CURDATE() + INTERVAL 1 DAY"

            # 今日出入库汇总
            today = {}
            for key, table, date_column in (
                ('inbound', 'inbound_records', 'inbound_date'),
                ('outbound', 'outbound_records', 'outbound_date'),
            ):
                cursor.execute(f"""
                    SELECT COALESCE(SUM(quantity), 0) AS quantity, COUNT(*) AS records
                    FROM {table}
                    WHERE {date_column} >= {today_start} AND {date_column} < {today_end}
                    {company_filter}
                """, company_params)
                row = cursor.fetchone() or {}
                today[f'{key}_quantity'] = int(row.get('quantity') or 0)
                today[f'{key}_records'] = int(row.get('records') or 0)

            # 近N天出入库趋势
            start_date = date.today() - timedelta(days=days - 1)
            trend_map = {}
            for key, table, date_column in (
                ('inbound', 'inbound_records', 'inbound_date'),
                ('outbound', 'outbound_records', 'outbound_date'),
            ):
                cursor.execute(f"""
                    SELECT DATE({date_column}) AS stat_date, COALESCE(SUM(quantity), 0) AS quantity
                    FROM {table}
                    WHERE {date_column} >= %s AND {date_column} < {today_end}
                    {company_filter}
                    GROUP BY DATE({date_column})
                """, (start_date,) + company_params)
                for row in cursor.fetchall():
                    stat_date = row.get('stat_date')
                    if not stat_date:
                        continue
                    if isinstance(stat_date, datetime):
                        stat_date = stat_date.date()
                    trend_map.setdefault(stat_date, {'inbound': 0, 'outbound': 0})
                    trend_map[stat_date][key] = int(row.get('quantity') or 0)

            trend_points = []
            for offset in range(days):
                current = start_date + timedelta(days=offset)
                values = trend_map.get(current, {'inbound': 0, 'outbound': 0})
                trend_points.append({
                    'date': current.strftime('%Y-%m-%d'),
                    'label': current.strftime('%m-%d'),
                    'inbound': values.get('inbound', 0),
                    'outbound': values.get('outbound', 0)
                })

            # 今日各品类出入库明细
            category_movement = {}
            for key, table, date_column in (
                ('inbound', 'inbound_records', 'inbound_date'),
                ('outbound', 'outbound_records', 'outbound_date'),
            ):
                cursor.execute(f"""
                    SELECT COALESCE(c.category_name, '未分类') AS category_name,
                           COALESCE(SUM(r.quantity), 0) AS quantity
                    FROM {table} r
                    LEFT JOIN device_models m ON r.model_id = m.id
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    WHERE r.{date_column} >= {today_start} AND r.{date_column} < {today_end}
                    {"AND r.company_id = %s" if company_id else ""}
                    GROUP BY category_name
                """, company_params)
                for row in cursor.fetchall():
                    name = row.get('category_name') or '未分类'
                    category_movement.setdefault(name, {'inbound_quantity': 0, 'outbound_quantity': 0})
                    category_movement[name][f'{key}_quantity'] = int(row.get('quantity') or 0)

            today_categories = [
                {
                    'category_name': name,
                    'inbound_quantity': values.get('inbound_quantity', 0),
                    'outbound_quantity': values.get('outbound_quantity', 0)
                }
                for name, values in category_movement.items()
                if values.get('inbound_quantity', 0) > 0 or values.get('outbound_quantity', 0) > 0
            ]
            today_categories.sort(
                key=lambda item: item['inbound_quantity'] + item['outbound_quantity'],
                reverse=True
            )

            # 仓库数量
            cursor.execute(
                "SELECT COUNT(*) FROM warehouse_locations" + (" WHERE company_id = %s" if company_id else ""),
                company_params
            )
            warehouse_count = cursor.fetchone()
            warehouse_count = int(list(warehouse_count.values())[0]) if warehouse_count else 0

            overview = self.get_statistics(company_id) or {}
            category_distribution = self.get_inventory_stats(company_id) or []

            return {
                'today': today,
                'trend': {
                    'days': days,
                    'points': trend_points,
                    'inbound_total': sum(p['inbound'] for p in trend_points),
                    'outbound_total': sum(p['outbound'] for p in trend_points)
                },
                'today_categories': today_categories,
                'overview': {
                    'total_models': int(overview.get('total_models') or 0),
                    'total_quantity': int(overview.get('total_quantity') or 0),
                    'total_available': int(overview.get('total_available') or 0),
                    'category_count': int(overview.get('category_count') or 0),
                    'store_count': int(overview.get('store_count') or 0),
                    'warehouse_count': warehouse_count,
                    'inbound_count': int(overview.get('inbound_count') or 0),
                    'outbound_count': int(overview.get('outbound_count') or 0)
                },
                'category_distribution': [
                    {
                        'category_name': item.get('category_name') or '未分类',
                        'quantity': int(item.get('total_quantity') or 0)
                    }
                    for item in category_distribution
                ],
                'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        except Exception as e:
            print(f"获取报表汇总数据失败: {e}")
            traceback.print_exc()
            return None
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @staticmethod
    def _parse_date_range(start_date, end_date, default_days=7):
        """解析日期范围参数，非法或缺失时默认最近N天（含今天）"""
        def _parse(value):
            if not value:
                return None
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S'):
                try:
                    return datetime.strptime(str(value).strip(), fmt).date()
                except ValueError:
                    continue
            return None

        end = _parse(end_date) or date.today()
        start = _parse(start_date) or (end - timedelta(days=max(default_days - 1, 0)))
        if start > end:
            start, end = end, start
        return start, end

    def get_operations_by_date_range(self, start_date, end_date, company_id=None, record_type='all', limit=None):
        """按日期范围查询出入库明细（含品类、用途、店号等），日期已格式化为字符串"""
        conn = None
        cursor = None
        try:
            start, end = self._parse_date_range(start_date, end_date)
            conn = self.get_connection()
            if not conn:
                print("查询出入库明细失败: 数据库连接失败")
                return []

            cursor = conn.cursor(pymysql.cursors.DictCursor)
            company_filter = " AND r.company_id = %s" if company_id else ""
            company_params = (company_id,) if company_id else ()
            results = []

            if record_type in ('all', 'inbound'):
                cursor.execute(f"""
                    SELECT '入库' AS record_type, r.id AS record_id, r.inbound_id AS biz_id,
                           r.quantity, r.inbound_date AS record_date, r.operator, r.notes,
                           r.serial_number, r.unit_code, r.start_use_date,
                           m.model_name, m.model_code,
                           COALESCE(c.category_name, '未分类') AS category_name,
                           COALESCE(p.type_name, '') AS purpose_name,
                           COALESCE(s.store_code, '') AS store_code,
                           COALESCE(s.store_name, '') AS store_name,
                           COALESCE(m.description, '') AS device_description,
                           COALESCE(wl.location_name, '') AS warehouse_name,
                           '' AS work_order_number
                    FROM inbound_records r
                    LEFT JOIN device_models m ON r.model_id = m.id
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    LEFT JOIN inbound_types p ON r.inbound_type_id = p.id
                    LEFT JOIN stores s ON r.store_id = s.id
                    LEFT JOIN warehouse_locations wl ON r.warehouse_location_id = wl.id
                    WHERE r.inbound_date >= %s AND r.inbound_date < DATE_ADD(%s, INTERVAL 1 DAY)
                    {company_filter}
                """, (start, end) + company_params)
                results.extend(cursor.fetchall())

            if record_type in ('all', 'outbound'):
                cursor.execute(f"""
                    SELECT '出库' AS record_type, r.id AS record_id, r.outbound_id AS biz_id,
                           r.quantity, r.outbound_date AS record_date, r.operator, r.notes,
                           r.serial_number, r.unit_code, r.start_use_date,
                           m.model_name, m.model_code,
                           COALESCE(c.category_name, '未分类') AS category_name,
                           COALESCE(p.purpose_name, '') AS purpose_name,
                           COALESCE(s.store_code, '') AS store_code,
                           COALESCE(s.store_name, '') AS store_name,
                           COALESCE(m.description, '') AS device_description,
                           COALESCE(wl.location_name, '') AS warehouse_name,
                           COALESCE(r.work_order_number, '') AS work_order_number
                    FROM outbound_records r
                    LEFT JOIN device_models m ON r.model_id = m.id
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    LEFT JOIN outbound_purposes p ON r.purpose_id = p.id
                    LEFT JOIN stores s ON r.store_id = s.id
                    LEFT JOIN warehouse_locations wl ON r.warehouse_location_id = wl.id
                    WHERE r.outbound_date >= %s AND r.outbound_date < DATE_ADD(%s, INTERVAL 1 DAY)
                    {company_filter}
                """, (start, end) + company_params)
                results.extend(cursor.fetchall())

            records = []
            for row in results:
                record_date = row.get('record_date')
                start_use = row.get('start_use_date')
                # 店号、店名分开存储；展示端按「店号 - 店名」格式组合
                store_code = row.get('store_code') or ''
                store_name = row.get('store_name') or ''
                # 历史导入数据可能存在字面量 'nan'/'null'，展示时视为空
                device_description = str(row.get('device_description') or '').strip()
                if device_description.lower() in ('nan', 'null', 'none'):
                    device_description = ''
                records.append({
                    'record_type': row.get('record_type'),
                    'record_id': row.get('record_id'),
                    'biz_id': row.get('biz_id') or '',
                    'date': record_date.strftime('%Y-%m-%d %H:%M') if isinstance(record_date, (datetime, date)) else '',
                    'category_name': row.get('category_name') or '未分类',
                    'model_name': row.get('model_name') or '',
                    'model_code': row.get('model_code') or '',
                    'quantity': int(row.get('quantity') or 0),
                    'purpose_name': row.get('purpose_name') or '',
                    'store_code': store_code,
                    'store_name': store_name,
                    'device_description': device_description,
                    'warehouse_name': row.get('warehouse_name') or '',
                    'operator': row.get('operator') or '',
                    'serial_number': row.get('serial_number') or '',
                    'unit_code': row.get('unit_code') or '',
                    'start_use_date': start_use.strftime('%Y-%m-%d') if isinstance(start_use, (datetime, date)) else '',
                    'work_order_number': row.get('work_order_number') or '',
                    'notes': row.get('notes') or ''
                })

            records.sort(key=lambda item: item['date'], reverse=True)
            if limit:
                records = records[:int(limit)]
            return records
        except Exception as e:
            print(f"查询出入库明细失败: {e}")
            traceback.print_exc()
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def get_operations_summary(self, start_date, end_date, company_id=None):
        """按日期范围汇总出入库数据：总量/笔数 + 按天 + 按品类"""
        conn = None
        cursor = None
        try:
            start, end = self._parse_date_range(start_date, end_date)
            conn = self.get_connection()
            if not conn:
                print("查询出入库汇总失败: 数据库连接失败")
                return None

            cursor = conn.cursor(pymysql.cursors.DictCursor)
            company_filter = " AND company_id = %s" if company_id else ""
            company_params = (company_id,) if company_id else ()

            summary = {
                'start_date': start.strftime('%Y-%m-%d'),
                'end_date': end.strftime('%Y-%m-%d'),
                'inbound_quantity': 0,
                'outbound_quantity': 0,
                'inbound_records': 0,
                'outbound_records': 0
            }
            daily_map = {}
            category_map = {}

            for key, table, date_column in (
                ('inbound', 'inbound_records', 'inbound_date'),
                ('outbound', 'outbound_records', 'outbound_date'),
            ):
                cursor.execute(f"""
                    SELECT COALESCE(SUM(quantity), 0) AS quantity, COUNT(*) AS records
                    FROM {table}
                    WHERE {date_column} >= %s AND {date_column} < DATE_ADD(%s, INTERVAL 1 DAY)
                    {company_filter}
                """, (start, end) + company_params)
                row = cursor.fetchone() or {}
                summary[f'{key}_quantity'] = int(row.get('quantity') or 0)
                summary[f'{key}_records'] = int(row.get('records') or 0)

                cursor.execute(f"""
                    SELECT DATE({date_column}) AS stat_date, COALESCE(SUM(quantity), 0) AS quantity,
                           COUNT(*) AS records
                    FROM {table}
                    WHERE {date_column} >= %s AND {date_column} < DATE_ADD(%s, INTERVAL 1 DAY)
                    {company_filter}
                    GROUP BY DATE({date_column})
                """, (start, end) + company_params)
                for item in cursor.fetchall():
                    stat_date = item.get('stat_date')
                    if isinstance(stat_date, datetime):
                        stat_date = stat_date.date()
                    if not stat_date:
                        continue
                    daily_map.setdefault(stat_date, {'inbound': 0, 'outbound': 0})
                    daily_map[stat_date][key] = int(item.get('quantity') or 0)

                cursor.execute(f"""
                    SELECT COALESCE(c.category_name, '未分类') AS category_name,
                           COALESCE(SUM(r.quantity), 0) AS quantity
                    FROM {table} r
                    LEFT JOIN device_models m ON r.model_id = m.id
                    LEFT JOIN device_categories c ON m.category_id = c.id
                    WHERE r.{date_column} >= %s AND r.{date_column} < DATE_ADD(%s, INTERVAL 1 DAY)
                    {"AND r.company_id = %s" if company_id else ""}
                    GROUP BY category_name
                """, (start, end) + company_params)
                for item in cursor.fetchall():
                    name = item.get('category_name') or '未分类'
                    category_map.setdefault(name, {'inbound_quantity': 0, 'outbound_quantity': 0})
                    category_map[name][f'{key}_quantity'] = int(item.get('quantity') or 0)

            daily = []
            total_days = (end - start).days + 1
            for offset in range(total_days):
                current = start + timedelta(days=offset)
                values = daily_map.get(current, {'inbound': 0, 'outbound': 0})
                daily.append({
                    'date': current.strftime('%Y-%m-%d'),
                    'inbound': values.get('inbound', 0),
                    'outbound': values.get('outbound', 0),
                    'total': values.get('inbound', 0) + values.get('outbound', 0)
                })
            daily.sort(key=lambda item: item['date'], reverse=True)
            summary['daily'] = daily

            categories = [
                {
                    'category_name': name,
                    'inbound_quantity': values.get('inbound_quantity', 0),
                    'outbound_quantity': values.get('outbound_quantity', 0),
                    'total_quantity': values.get('inbound_quantity', 0) + values.get('outbound_quantity', 0)
                }
                for name, values in category_map.items()
                if values.get('inbound_quantity', 0) > 0 or values.get('outbound_quantity', 0) > 0
            ]
            categories.sort(key=lambda item: item['total_quantity'], reverse=True)
            summary['categories'] = categories
            summary['total_quantity'] = summary['inbound_quantity'] + summary['outbound_quantity']
            summary['total_records'] = summary['inbound_records'] + summary['outbound_records']
            return summary
        except Exception as e:
            print(f"查询出入库汇总失败: {e}")
            traceback.print_exc()
            return None
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # 搜索方法
    def search_models(self, keyword, company_id=None):
        """搜索设备型号"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT m.id, m.model_name, m.model_code, m.description, c.category_name
                    FROM device_models m
                    JOIN device_categories c ON m.category_id = c.id
                    WHERE (m.model_name LIKE %s OR m.model_code LIKE %s OR c.category_name LIKE %s OR m.description LIKE %s) AND m.company_id = %s
                    ORDER BY m.model_name
                    LIMIT 20
                """, (f'%{keyword}%', f'%{keyword}%', f'%{keyword}%', f'%{keyword}%', company_id))
            else:
                cursor.execute("""
                    SELECT m.id, m.model_name, m.model_code, m.description, c.category_name
                    FROM device_models m
                    JOIN device_categories c ON m.category_id = c.id
                    WHERE m.model_name LIKE %s OR m.model_code LIKE %s OR c.category_name LIKE %s OR m.description LIKE %s
                    ORDER BY m.model_name
                    LIMIT 20
                """, (f'%{keyword}%', f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'))
            return cursor.fetchall()
        except Exception as e:
            print(f"搜索设备型号失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def get_all_device_units(self, company_id=None):
        """获取所有设备单元"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT u.id, u.unit_code, u.serial_number, u.status, m.model_name, m.model_code, c.category_name
                    FROM device_units u
                    JOIN device_models m ON u.model_id = m.id
                    JOIN device_categories c ON m.category_id = c.id
                    WHERE u.company_id = %s
                    ORDER BY u.unit_code
                    LIMIT 100
                """, (company_id,))
            else:
                cursor.execute("""
                    SELECT u.id, u.unit_code, u.serial_number, u.status, m.model_name, m.model_code, c.category_name
                    FROM device_units u
                    JOIN device_models m ON u.model_id = m.id
                    JOIN device_categories c ON m.category_id = c.id
                    ORDER BY u.unit_code
                    LIMIT 100
                """)
            return cursor.fetchall()
        except Exception as e:
            print(f"获取设备单元失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def search_device_units(self, keyword, company_id=None):
        """搜索设备单元（序列号/资产编号）"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT u.id, u.unit_code, u.serial_number, u.status, m.model_name, m.model_code, c.category_name
                    FROM device_units u
                    JOIN device_models m ON u.model_id = m.id
                    JOIN device_categories c ON m.category_id = c.id
                    WHERE (u.unit_code LIKE %s OR u.serial_number LIKE %s) AND u.company_id = %s
                    ORDER BY u.unit_code
                    LIMIT 20
                """, (f'%{keyword}%', f'%{keyword}%', company_id))
            else:
                cursor.execute("""
                    SELECT u.id, u.unit_code, u.serial_number, u.status, m.model_name, m.model_code, c.category_name
                    FROM device_units u
                    JOIN device_models m ON u.model_id = m.id
                    JOIN device_categories c ON m.category_id = c.id
                    WHERE u.unit_code LIKE %s OR u.serial_number LIKE %s
                    ORDER BY u.unit_code
                    LIMIT 20
                """, (f'%{keyword}%', f'%{keyword}%'))
            return cursor.fetchall()
        except Exception as e:
            print(f"搜索设备单元失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def search_stores(self, keyword, company_id=None):
        """搜索店号店名"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT id, store_code, store_name, contact_person, phone
                    FROM stores
                    WHERE (store_code LIKE %s OR store_name LIKE %s OR contact_person LIKE %s) AND company_id = %s
                    ORDER BY store_code
                    LIMIT 20
                """, (f'%{keyword}%', f'%{keyword}%', f'%{keyword}%', company_id))
            else:
                cursor.execute("""
                    SELECT id, store_code, store_name, contact_person, phone
                    FROM stores
                    WHERE store_code LIKE %s OR store_name LIKE %s OR contact_person LIKE %s
                    ORDER BY store_code
                    LIMIT 20
                """, (f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'))
            return cursor.fetchall()
        except Exception as e:
            print(f"搜索店号店名失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def search_warehouse_locations(self, keyword, company_id=None):
        """搜索仓库位置"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if not conn:
                return []
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            if company_id:
                cursor.execute("""
                    SELECT id, location_name, location_code
                    FROM warehouse_locations
                    WHERE (location_name LIKE %s OR location_code LIKE %s) AND company_id = %s
                    ORDER BY location_name
                    LIMIT 20
                """, (f'%{keyword}%', f'%{keyword}%', company_id))
            else:
                cursor.execute("""
                    SELECT id, location_name, location_code
                    FROM warehouse_locations
                    WHERE location_name LIKE %s OR location_code LIKE %s
                    ORDER BY location_name
                    LIMIT 20
                """, (f'%{keyword}%', f'%{keyword}%'))
            return cursor.fetchall()
        except Exception as e:
            print(f"搜索仓库位置失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

# 创建数据库管理器实例
db_manager = DatabaseManager()

@app.teardown_request
def _release_db_connection(exception=None):
    """请求结束（含异常）时强制释放共享数据库连接。

    原先 38 处路由都是 connect() → get_xxx() → close() 的顺序写法，且没有 try/finally，
    中间任一步抛异常都会跳过 close()，使共享连接滞留、并在下次 connect() 时被覆盖丢弃，
    长期运行造成连接/句柄泄漏。这里用 teardown 兜底，保证每个请求结束时一定释放。
    """
    try:
        db_manager.close()
    except Exception as e:
        print(f"释放数据库连接失败: {e}")

# 全局系统设置
system_settings = {"timezone": "Asia/Shanghai", "language": "zh-CN"}


def export_mysql_to_file(filepath):
    """将MySQL数据库导出为SQL文件"""
    conn = db_manager.get_connection()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f'-- MySQL dump for {db_manager.database}\n')
            f.write(f'-- Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n\n')

            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]

            for table in tables:
                cursor.execute(f"SHOW CREATE TABLE `{table}`")
                create_stmt = cursor.fetchone()[1]
                f.write(f'DROP TABLE IF EXISTS `{table}`;\n')
                f.write(f'{create_stmt};\n\n')

                cursor.execute(f"SELECT * FROM `{table}`")
                rows = cursor.fetchall()
                if rows:
                    cursor.execute(f"SHOW COLUMNS FROM `{table}`")
                    columns = [col[0] for col in cursor.fetchall()]
                    col_names = ', '.join([f'`{col}`' for col in columns])

                    for row in rows:
                        values = []
                        for val in row:
                            if val is None:
                                values.append('NULL')
                            elif isinstance(val, (int, float)):
                                values.append(str(val))
                            elif isinstance(val, (datetime, date)):
                                values.append(f"'{val.strftime('%Y-%m-%d %H:%M:%S')}'")
                            else:
                                escaped = str(val).replace("'", "''").replace('\\', '\\\\')
                                values.append(f"'{escaped}'")
                        f.write(f'INSERT INTO `{table}` ({col_names}) VALUES ({", ".join(values)});\n')
                    f.write('\n')

            f.write('-- Dump completed\n')
        return True
    except Exception as e:
        print(f"MySQL导出失败: {e}")
        return False
    finally:
        cursor.close()
        conn.close()

# 初始化系统设置
def init_system_settings():
    """初始化系统设置"""
    global system_settings
    try:
        # 尝试获取系统设置
        settings = db_manager.get_system_settings()
        if settings:
            system_settings = settings
            print(f"系统设置初始化完成: 时区={system_settings['timezone']}, 语言={system_settings['language']}")
        else:
            # 使用默认设置
            print("使用默认系统设置")
    except Exception as e:
        print(f"系统设置初始化失败，使用默认设置: {e}")

# 初始化数据库
print("开始初始化数据库...")
if db_manager.connect():
    db_manager.create_tables()
    db_manager.init_default_data()
    db_manager.close()
    print("数据库初始化完成")
else:
    print("数据库初始化失败，应用将继续运行，但部分功能可能受限")

# 在应用启动时初始化系统设置
init_system_settings()

# API路由
@app.route('/api/stats')
def api_stats():
    """获取统计数据API"""
    company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        return jsonify({'success': False})
    
    stats = db_manager.get_statistics(company_id)
    db_manager.close()
    
    if stats:
        stats['success'] = True
        return jsonify(stats)
    else:
        return jsonify({'success': False, 'last_update': '-'})

@app.route('/api/stats/inventory')
def api_inventory_stats():
    """获取库存统计数据API"""
    company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        return jsonify({'success': False, 'stats': []})
    
    stats_data = db_manager.get_inventory_stats(company_id)
    db_manager.close()
    
    if stats_data is not None:
        return jsonify({'success': True, 'stats': stats_data})
    else:
        return jsonify({'success': False, 'stats': []})

@app.route('/api/reports/summary')
@require_auth
def api_report_summary():
    """报表汇总API：今日出入库、近N天趋势、今日各品类明细、库存概况"""
    company_id = get_current_company_id(for_query=True)
    days = request.args.get('days', 7, type=int)

    if not db_manager.connect():
        return jsonify({'success': False, 'message': '数据库连接失败'})

    try:
        summary = db_manager.get_report_summary(company_id, days)
    finally:
        db_manager.close()

    if not summary:
        return jsonify({'success': False, 'message': '获取报表数据失败'}), 500

    summary['success'] = True
    return jsonify(summary)

@app.route('/api/reports/recent')
@require_auth
def api_report_recent():
    """近N天出入库明细与汇总API（App 报表页使用）"""
    company_id = get_current_company_id(for_query=True)
    days = max(1, min(request.args.get('days', 7, type=int) or 7, 90))
    limit = max(1, min(request.args.get('limit', 300, type=int) or 300, 2000))
    record_type = (request.args.get('type') or 'all').lower()
    if record_type not in ('all', 'inbound', 'outbound'):
        record_type = 'all'

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    records = db_manager.get_operations_by_date_range(
        start_date, end_date, company_id, record_type, limit=limit)
    summary = db_manager.get_operations_summary(start_date, end_date, company_id)
    if summary is None:
        return jsonify({'success': False, 'message': '获取出入库数据失败'}), 500

    return jsonify({
        'success': True,
        'summary': summary,
        'records': records,
        'days': days,
        'limit': limit,
        'type': record_type
    })

@app.route('/api/categories', methods=['GET', 'POST', 'DELETE'])
@require_auth
def api_categories():
    """设备分类API"""
    if not db_manager.connect():
        return jsonify([])
    
    company_id = get_current_company_id(for_query=True)
    
    if request.method == 'GET':
        categories = db_manager.get_all_categories(company_id)
        db_manager.close()
        return jsonify(categories)
    
    elif request.method == 'POST':
        data = request.get_json()
        category_name = data.get('category_name', '').strip()
        description = data.get('description', '').strip()
        
        if not category_name:
            db_manager.close()
            return jsonify({'error': '分类名称不能为空'}), 400
        
        success = db_manager.add_category(category_name, description, company_id)
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '添加分类失败'}), 500
    
    elif request.method == 'DELETE':
        category_id = request.args.get('id')
        if not category_id:
            db_manager.close()
            return jsonify({'error': '分类ID不能为空'}), 400
        
        success, message = db_manager.delete_category(int(category_id))
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': message}), 400


@app.route('/api/models', methods=['GET', 'POST', 'PUT', 'DELETE'])
@require_auth
def api_models():
    """设备型号API"""
    if not db_manager.connect():
        return jsonify([])
    
    if request.method == 'GET':
        company_id = get_current_company_id(for_query=True)
        models = db_manager.get_all_models(company_id)
        db_manager.close()
        return jsonify(models)
    
    elif request.method == 'POST':
        company_id = get_current_company_id()
        data = request.get_json()
        category_id = data.get('category_id')
        model_name = data.get('model_name', '').strip()
        model_code = data.get('model_code', '').strip()
        description = data.get('description', '').strip()
        specifications = data.get('specifications', '').strip()
        
        if not category_id:
            db_manager.close()
            return jsonify({'error': '请选择设备分类'}), 400
        if not model_name:
            db_manager.close()
            return jsonify({'error': '型号名称不能为空'}), 400
        if not model_code:
            db_manager.close()
            return jsonify({'error': '型号代码不能为空'}), 400
        
        success = db_manager.add_model(category_id, model_name, model_code, description, specifications, company_id)
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '添加型号失败'}), 500
    
    elif request.method == 'DELETE':
        model_id = request.args.get('id')
        if not model_id:
            db_manager.close()
            return jsonify({'error': '型号ID不能为空'}), 400
        
        success = db_manager.delete_model(int(model_id))
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '删除型号失败'}), 400
    
    elif request.method == 'PUT':
        data = request.get_json()
        model_id = data.get('model_id')
        category_id = data.get('category_id')
        model_name = data.get('model_name', '').strip()
        model_code = data.get('model_code', '').strip()
        description = data.get('description', '').strip()
        specifications = data.get('specifications', '').strip()
        
        if not model_id:
            db_manager.close()
            return jsonify({'error': '缺少型号ID'}), 400
        if not category_id:
            db_manager.close()
            return jsonify({'error': '请选择设备分类'}), 400
        if not model_name:
            db_manager.close()
            return jsonify({'error': '型号名称不能为空'}), 400
        if not model_code:
            db_manager.close()
            return jsonify({'error': '型号代码不能为空'}), 400
        
        success = db_manager.update_model(int(model_id), int(category_id), model_name, model_code, description, specifications)
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '更新型号失败'}), 400

@app.route('/api/units', methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/api/units/<int:unit_id>', methods=['GET'])
@require_auth
def api_units(unit_id=None):
    """设备编号API"""
    if not db_manager.connect():
        return jsonify([])
    
    if request.method == 'GET':
        company_id = get_current_company_id(for_query=True)
        units = db_manager.get_all_units(company_id)
        db_manager.close()
        return jsonify(units)
    
    elif request.method == 'POST':
        company_id = get_current_company_id()
        data = request.get_json()
        model_id = data.get('model_id')
        unit_code = data.get('unit_code', '').strip()
        serial_number = data.get('serial_number', '').strip()
        purchase_date = data.get('purchase_date')
        purchase_price = data.get('purchase_price', 0)
        location = data.get('location', '').strip()
        notes = data.get('notes', '').strip()
        
        if not model_id:
            db_manager.close()
            return jsonify({'error': '请选择设备型号'}), 400
        if not unit_code:
            db_manager.close()
            return jsonify({'error': '设备编号不能为空'}), 400
        
        success = db_manager.add_device_unit(model_id, unit_code, serial_number, '在库', 
                                           purchase_date, purchase_price, location, notes, company_id)
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '添加设备失败'}), 500
    
    elif request.method == 'PUT':
        data = request.get_json()
        unit_id = data.get('id')
        status = data.get('status', '').strip()
        location = data.get('location', '').strip()
        notes = data.get('notes', '').strip()
        
        if not unit_id:
            db_manager.close()
            return jsonify({'error': '设备ID不能为空'}), 400
        
        success = db_manager.update_device_unit(unit_id, status, location, notes)
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '更新设备失败'}), 400
    
    elif request.method == 'DELETE':
        unit_id = request.args.get('id')
        if not unit_id:
            db_manager.close()
            return jsonify({'error': '设备ID不能为空'}), 400
        
        success = db_manager.delete_unit(int(unit_id))
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '删除设备失败'}), 400



@app.route('/export/inbound')
@login_required
def export_inbound():
    """导出入库记录"""
    company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        return jsonify([])
    
    inbound_records = db_manager.get_inbound_records(company_id)
    db_manager.close()
    
    # 生成CSV
    import csv
    from io import StringIO
    
    csv_data = StringIO()
    fieldnames = ['id', 'inbound_id', 'model_id', 'category_name', 'model_code', 'model_name', 'description', 'quantity', 'operator', 'inbound_date', 'created_at', 'purpose_name', 'supplier', 'location', 'warehouse_location_name', 'store_code', 'store_name', 'start_use_date', 'notes', 'serial_number', 'unit_code']
    writer = csv.DictWriter(csv_data, fieldnames=fieldnames)
    
    writer.writeheader()
    for record in inbound_records:
        # 只写入fieldnames中定义的字段，忽略其他字段
        writer.writerow({key: record.get(key, '') for key in fieldnames})
    
    # 返回CSV文件
    from flask import make_response
    response = make_response(csv_data.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=inbound_records_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    return response

@app.route('/export/outbound')
@login_required
def export_outbound():
    """导出出库记录"""
    company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        return jsonify([])
    
    outbound_records = db_manager.get_outbound_records(company_id)
    db_manager.close()
    
    # 生成CSV
    import csv
    from io import StringIO
    
    csv_data = StringIO()
    fieldnames = ['id', 'outbound_id', 'model_id', 'category_name', 'model_code', 'model_name',  'description', 'quantity', 'operator', 'outbound_date', 'created_at', 'purpose_name', 'warehouse_location_name', 'store_code', 'store_name', 'start_use_date', 'notes', 'serial_number', 'unit_code', 'work_order_number']
    writer = csv.DictWriter(csv_data, fieldnames=fieldnames)
    
    writer.writeheader()
    for record in outbound_records:
        # 只写入fieldnames中定义的字段，忽略其他字段
        writer.writerow({key: record.get(key, '') for key in fieldnames})
    
    # 返回CSV文件
    from flask import make_response
    response = make_response(csv_data.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=outbound_records_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    return response

@app.route('/export/inventory')
@login_required
def export_inventory():
    """导出库存数据"""
    company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        return jsonify([])
    
    inventory = db_manager.get_inventory(company_id)
    db_manager.close()
    
    # 生成CSV
    import csv
    from io import StringIO
    
    csv_data = StringIO()
    fieldnames = ['id', 'category_name', 'model_name', 'model_code', 'description', 'total_quantity', 'available_quantity', 'last_inbound_date', 'last_outbound_date', 'created_at', 'updated_at']
    writer = csv.DictWriter(csv_data, fieldnames=fieldnames)
    
    writer.writeheader()
    for record in inventory:
        # 只写入fieldnames中定义的字段，忽略其他字段
        writer.writerow({key: record.get(key, '') for key in fieldnames})
    
    # 返回CSV文件
    from flask import make_response
    response = make_response(csv_data.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename=inventory_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    return response

# 导出明细字段定义（键 -> 中文表头）；入库与出库分表展示，故不含“类型”列；店号与店名分列导出
RECORD_DETAIL_COLUMNS = [
    ('date', '日期'),
    ('category_name', '品类'),
    ('model_code', '型号代码'),
    ('quantity', '数量'),
    ('store_code', '店号'),
    ('store_name', '店名'),
    ('start_use_date', '开始使用日期'),
    ('model_name', '型号名称'),
    ('purpose_name', '用途'),
    ('work_order_number', '工单编号'),
    ('notes', '备注'),
    ('warehouse_name', '仓库位置'),
    ('operator', '操作人'),
    ('serial_number', '序列号'),
    ('unit_code', '资产编号'),
    ('biz_id', '单据号'),
]

PDF_MAX_ROWS = 3000


def _split_records(records):
    """按类型拆分出入库明细"""
    inbound = [record for record in records if record.get('record_type') == '入库']
    outbound = [record for record in records if record.get('record_type') == '出库']
    return inbound, outbound


def _write_detail_sheet(writer, sheet_name, rows):
    """写入一个明细工作表：始终写表头，无数据时写入占位说明"""
    import pandas as pd
    from openpyxl.utils import get_column_letter

    labels = [label for _, label in RECORD_DETAIL_COLUMNS]
    frame = pd.DataFrame(
        [{label: record.get(key) for key, label in RECORD_DETAIL_COLUMNS} for record in rows],
        columns=labels
    )
    frame.to_excel(writer, sheet_name=sheet_name, index=False)
    sheet = writer.sheets[sheet_name]

    for index, label in enumerate(labels, start=1):
        values = [len(str(label))] + [len(str(record.get(label, '') or '')) for record in rows[:200]]
        sheet.column_dimensions[get_column_letter(index)].width = min(max(max(values) + 4, 10), 40)

    if frame.empty:
        sheet.cell(row=2, column=1, value='该时间段内无记录')
    else:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(labels))}{len(frame) + 1}"


def _build_records_workbook(summary, records, record_type='all'):
    """生成出入库 Excel：汇总 + 入库明细 + 出库明细（入库与出库分开）"""
    import pandas as pd
    from io import BytesIO

    inbound_records, outbound_records = _split_records(records)

    summary_rows = [
        {'项目': '统计区间', '值': f"{summary['start_date']} ~ {summary['end_date']}"},
        {'项目': '入库总量', '值': summary['inbound_quantity']},
        {'项目': '入库笔数', '值': summary['inbound_records']},
        {'项目': '出库总量', '值': summary['outbound_quantity']},
        {'项目': '出库笔数', '值': summary['outbound_records']},
        {'项目': '出入库合计', '值': summary['total_quantity']},
        {'项目': '入库明细条数', '值': len(inbound_records)},
        {'项目': '出库明细条数', '值': len(outbound_records)},
    ]
    for item in summary.get('daily', []):
        summary_rows.append({
            '项目': f"按天-{item['date']}",
            '值': f"入库 {item['inbound']} / 出库 {item['outbound']}"
        })
    for item in summary.get('categories', []):
        summary_rows.append({
            '项目': f"品类-{item['category_name']}",
            '值': f"入库 {item['inbound_quantity']} / 出库 {item['outbound_quantity']}"
        })

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        pd.DataFrame(summary_rows, columns=['项目', '值']).to_excel(
            writer, sheet_name='汇总', index=False)
        summary_sheet = writer.sheets['汇总']
        summary_sheet.column_dimensions['A'].width = 22
        summary_sheet.column_dimensions['B'].width = 42

        if record_type in ('all', 'inbound'):
            _write_detail_sheet(writer, '入库明细', inbound_records)
        if record_type in ('all', 'outbound'):
            _write_detail_sheet(writer, '出库明细', outbound_records)
    buffer.seek(0)
    return buffer


def _build_records_pdf(summary, records, record_type='all'):
    """生成出入库 PDF 报表：汇总 + 入库明细 + 出库明细（入库与出库分开）"""
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))

    title_style = ParagraphStyle('CnTitle', fontName='STSong-Light', fontSize=16, leading=22, spaceAfter=6)
    section_style = ParagraphStyle('CnSection', fontName='STSong-Light', fontSize=12, leading=18,
                                   spaceBefore=10, spaceAfter=4, textColor=colors.HexColor('#1565C0'))
    info_style = ParagraphStyle('CnInfo', fontName='STSong-Light', fontSize=9, leading=14)
    cell_style = ParagraphStyle('CnCell', fontName='STSong-Light', fontSize=8, leading=11)
    header_style = ParagraphStyle('CnHeader', fontName='STSong-Light', fontSize=9, leading=12,
                                  textColor=colors.white)

    inbound_records, outbound_records = _split_records(records)

    elements = [
        Paragraph('出入库记录报表', title_style),
        Paragraph(f"统计区间：{summary['start_date']} ~ {summary['end_date']}", info_style),
        Paragraph(
            f"入库：{summary['inbound_quantity']} 件 / {summary['inbound_records']} 笔　　"
            f"出库：{summary['outbound_quantity']} 件 / {summary['outbound_records']} 笔　　"
            f"合计：{summary['total_quantity']} 件",
            info_style
        ),
        Spacer(1, 4)
    ]

    table_header = ['日期', '品类', '型号', '数量', '用途', '店号', '店名', '仓库位置', '操作人', '开始使用日期', '工单编号', '备注']
    col_widths = [64, 46, 104, 28, 50, 48, 50, 56, 42, 72, 70, 84]

    def append_section(title, section_records):
        elements.append(Paragraph(f"{title}（{len(section_records)} 条）", section_style))
        if not section_records:
            elements.append(Paragraph('该时间段内无记录', info_style))
            return
        rows = section_records[:PDF_MAX_ROWS]
        if len(section_records) > PDF_MAX_ROWS:
            elements.append(Paragraph(
                f"（记录较多，仅展示前 {PDF_MAX_ROWS} 条，完整数据请导出 Excel）", info_style))
        table_data = [[Paragraph(text, header_style) for text in table_header]]
        for record in rows:
            row = [
                record.get('date', ''),
                record.get('category_name', ''),
                f"{record.get('model_name', '')} {record.get('model_code', '')}".strip(),
                str(record.get('quantity', '')),
                record.get('purpose_name', ''),
                record.get('store_code', ''),
                record.get('store_name', ''),
                record.get('warehouse_name', ''),
                record.get('operator', ''),
                record.get('start_use_date', ''),
                record.get('work_order_number', ''),
                record.get('notes', '')
            ]
            table_data.append([Paragraph(str(value or ''), cell_style) for value in row])

        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1976D2')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#B0BEC5')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F7FA')]),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(table)

    if record_type in ('all', 'inbound'):
        append_section('一、入库明细', inbound_records)
    if record_type in ('all', 'outbound'):
        append_section('二、出库明细' if record_type == 'all' else '出库明细', outbound_records)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title='出入库记录报表'
    )
    doc.build(elements)
    buffer.seek(0)
    return buffer


@app.route('/export/records')
@login_required
def export_records():
    """按日期范围导出出入库记录（Excel / PDF）"""
    from flask import send_file

    start_arg = request.args.get('start')
    end_arg = request.args.get('end')
    export_format = (request.args.get('format') or 'xlsx').lower()
    record_type = (request.args.get('type') or 'all').lower()
    if record_type not in ('all', 'inbound', 'outbound'):
        record_type = 'all'
    if export_format not in ('xlsx', 'excel', 'pdf'):
        export_format = 'xlsx'

    start, end = db_manager._parse_date_range(start_arg, end_arg)
    if (end - start).days > 366:
        return jsonify({'success': False, 'message': '导出区间不能超过 366 天，请缩小日期范围'}), 400

    company_id = get_current_company_id(for_query=True)
    records = db_manager.get_operations_by_date_range(start, end, company_id, record_type)
    summary = db_manager.get_operations_summary(start, end, company_id)
    if summary is None:
        return jsonify({'success': False, 'message': '获取导出数据失败'}), 500

    # 诊断：汇总有数据但明细为空，通常说明明细查询异常，写入日志便于排查
    expected_records = {
        'all': summary.get('total_records', 0),
        'inbound': summary.get('inbound_records', 0),
        'outbound': summary.get('outbound_records', 0)
    }[record_type]
    if expected_records > 0 and not records:
        print(f"[导出告警] 区间 {start} ~ {end}（{record_type}）汇总有 {expected_records} 条记录，"
              f"但明细查询返回 0 条，请检查数据库查询日志")

    period = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    type_label = {'all': '出入库', 'inbound': '入库', 'outbound': '出库'}[record_type]

    try:
        if export_format == 'pdf':
            buffer = _build_records_pdf(summary, records, record_type)
            return send_file(
                buffer, as_attachment=True,
                download_name=f'{type_label}记录_{period}.pdf',
                mimetype='application/pdf'
            )
        buffer = _build_records_workbook(summary, records, record_type)
        return send_file(
            buffer, as_attachment=True,
            download_name=f'{type_label}记录_{period}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except ImportError as e:
        print(f"导出失败，缺少依赖: {e}")
        return jsonify({
            'success': False,
            'message': '服务器缺少导出组件，请先执行 pip install openpyxl reportlab 后重试'
        }), 500
    except Exception as e:
        print(f"导出失败: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'导出失败: {e}'}), 500


@app.route('/api/inventory/stock')
@require_auth
def api_inventory_stock():
    """各型号当前可用库存API（出库界面实时展示剩余库存）"""
    company_id = get_current_company_id(for_query=True)
    return jsonify(db_manager.get_model_stock(company_id))

@app.route('/api/inventory', methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/api/inventory/<int:inventory_id>', methods=['GET'])
@require_auth
def api_inventory(inventory_id=None):
    """库存管理API"""
    if not db_manager.connect():
        return jsonify([])
    
    if request.method == 'GET':
        company_id = get_current_company_id(for_query=True)
        inventory = db_manager.get_inventory(company_id)
        db_manager.close()
        return jsonify(inventory)
    
    elif request.method == 'POST':
        company_id = get_current_company_id()
        data = request.get_json()
        model_id = data.get('model_id')
        total_quantity = data.get('total_quantity')
        available_quantity = data.get('available_quantity')
        
        if not model_id:
            db_manager.close()
            return jsonify({'error': '请选择设备型号'}), 400
        if total_quantity is None or total_quantity < 0:
            db_manager.close()
            return jsonify({'error': '总数量必须大于等于0'}), 400
        if available_quantity is None or available_quantity < 0:
            db_manager.close()
            return jsonify({'error': '可用数量必须大于等于0'}), 400
        if available_quantity > total_quantity:
            db_manager.close()
            return jsonify({'error': '可用数量不能大于总数量'}), 400
        
        success = db_manager.add_inventory(int(model_id), int(total_quantity), int(available_quantity), company_id)
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '添加库存失败'}), 500
    
    elif request.method == 'PUT':
        data = request.get_json()
        inventory_id = data.get('id')
        total_quantity = data.get('total_quantity')
        available_quantity = data.get('available_quantity')
        
        if not inventory_id:
            db_manager.close()
            return jsonify({'error': '库存ID不能为空'}), 400
        
        success = db_manager.update_inventory(int(inventory_id), total_quantity, available_quantity)
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '更新库存失败'}), 400
    
    elif request.method == 'DELETE':
        inventory_id = request.args.get('id')
        if not inventory_id:
            db_manager.close()
            return jsonify({'error': '库存ID不能为空'}), 400
        
        success = db_manager.delete_inventory(int(inventory_id))
        db_manager.close()
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '删除库存失败'}), 400

@app.route('/api/alerts', methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/api/alerts/<int:alert_id>', methods=['GET'])
@require_auth
def api_alerts(alert_id=None):
    """库存告警管理API"""
    if not db_manager.connect():
        return jsonify([])
    
    query_company_id = get_current_company_id(for_query=True)
    company_id = get_current_company_id()
    
    if request.method == 'GET':
        if alert_id:
            # 获取单个告警详情
            alert = db_manager.get_stock_alert_by_id(alert_id)
            if alert:
                db_manager.close()
                return jsonify({'success': True, 'alert': alert})
            else:
                db_manager.close()
                return jsonify({'success': False, 'message': '告警设置不存在'})
        else:
            # 获取所有告警设置
            alerts = db_manager.get_stock_alerts(query_company_id)
            db_manager.close()
            return jsonify(alerts)
    
    elif request.method == 'POST':
        # 添加告警设置
        try:
            data = request.get_json()
            if not data:
                db_manager.close()
                return jsonify({'success': False, 'message': '请求数据格式错误'})
            
            model_id = data.get('model_id')
            threshold = data.get('threshold')
            alert_message = data.get('alert_message', '库存不足')
            is_active = data.get('is_active', True)
            
            if not model_id:
                db_manager.close()
                return jsonify({'success': False, 'message': '请选择设备型号'})
            
            # 转换threshold为整数
            threshold_int = int(threshold)
            if threshold_int < 0:
                db_manager.close()
                return jsonify({'success': False, 'message': '告警阈值必须大于等于0'})
            
            success = db_manager.add_stock_alert(int(model_id), threshold_int, alert_message, is_active, company_id)
            db_manager.close()
            
            if success:
                return jsonify({'success': True, 'message': '添加告警设置成功'})
            else:
                return jsonify({'success': False, 'message': '添加告警设置失败'})
        except ValueError as e:
            db_manager.close()
            return jsonify({'success': False, 'message': f'阈值必须是有效的整数: {str(e)}'})
        except Exception as e:
            db_manager.close()
            print(f"添加告警设置错误: {e}")
            return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'})
    
    elif request.method == 'PUT':
        # 更新告警设置
        try:
            data = request.get_json()
            if not data:
                db_manager.close()
                return jsonify({'success': False, 'message': '请求数据格式错误'})
            
            alert_id = data.get('id')
            threshold = data.get('threshold')
            alert_message = data.get('alert_message')
            is_active = data.get('is_active')
            
            if not alert_id:
                db_manager.close()
                return jsonify({'success': False, 'message': '告警ID不能为空'})
            
            # 转换threshold为整数
            threshold_int = int(threshold) if threshold is not None else None
            
            success = db_manager.update_stock_alert(int(alert_id), threshold_int, alert_message, is_active)
            db_manager.close()
            
            if success:
                return jsonify({'success': True, 'message': '更新告警设置成功'})
            else:
                return jsonify({'success': False, 'message': '更新告警设置失败'})
        except ValueError as e:
            db_manager.close()
            return jsonify({'success': False, 'message': f'阈值必须是有效的整数: {str(e)}'})
        except Exception as e:
            db_manager.close()
            print(f"更新告警设置错误: {e}")
            return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'})
    
    elif request.method == 'DELETE':
        # 删除告警设置
        try:
            alert_id = request.args.get('id')
            if not alert_id:
                db_manager.close()
                return jsonify({'success': False, 'message': '告警ID不能为空'})
            
            success = db_manager.delete_stock_alert(int(alert_id))
            db_manager.close()
            
            if success:
                return jsonify({'success': True, 'message': '删除告警设置成功'})
            else:
                return jsonify({'success': False, 'message': '删除告警设置失败'})
        except Exception as e:
            db_manager.close()
            print(f"删除告警设置错误: {e}")
            return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'})

@app.route('/api/alerts/check', methods=['GET'])
@require_auth
def api_check_alerts():
    """检查库存告警"""
    try:
        if not db_manager.connect():
            return jsonify([])
        alerts = db_manager.check_stock_alerts()
        db_manager.close()
        return jsonify(alerts)
    except Exception as e:
        db_manager.close()
        print(f"检查库存告警错误: {e}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'})

@app.route('/api/operators', methods=['GET', 'POST', 'DELETE'])
@require_auth
def api_operators():
    """操作人员管理API"""
    if not db_manager.connect():
        return jsonify([])
    
    query_company_id = get_current_company_id(for_query=True)
    
    if request.method == 'GET':
        # 获取所有操作人员
        operators = db_manager.get_all_operators(query_company_id)
        db_manager.close()
        return jsonify(operators)
    
    elif request.method == 'POST':
        # 添加操作人员
        data = request.get_json()
        operator_name = data.get('operator_name', '').strip()
        description = data.get('description', '').strip()
        
        if not operator_name:
            db_manager.close()
            return jsonify({'success': False, 'message': '操作人员姓名不能为空'})
        
        success = db_manager.add_operator(operator_name, description, company_id)
        db_manager.close()
        
        if success:
            return jsonify({'success': True, 'message': '操作人员添加成功'})
        else:
            return jsonify({'success': False, 'message': '操作人员添加失败'})
    
    elif request.method == 'DELETE':
        # 删除操作人员
        operator_id = request.args.get('id')
        if not operator_id:
            db_manager.close()
            return jsonify({'success': False, 'message': '操作人员ID不能为空'})
        
        success = db_manager.delete_operator(int(operator_id))
        db_manager.close()
        
        if success:
            return jsonify({'success': True, 'message': '操作人员删除成功'})
        else:
            return jsonify({'success': False, 'message': '操作人员删除失败'})

@app.route('/api/inbound', methods=['GET', 'POST'])
@app.route('/api/inbound/purpose', methods=['GET', 'POST', 'DELETE'])
@app.route('/api/inbound/batch', methods=['POST'])
@require_auth
def api_inbound():
    """入库管理API"""
    company_id = get_current_company_id()
    query_company_id = get_current_company_id(for_query=True)
    
    if request.path.endswith('/purpose'):
        if request.method == 'GET':
            # 获取入库用途列表 - 每次都重新连接
            if not db_manager.connect():
                return jsonify([])
            purposes = db_manager.get_inbound_purposes(query_company_id)
            db_manager.close()
            return jsonify(purposes)
        elif request.method == 'POST':
            # 添加入库用途 - 每次都重新连接
            if not db_manager.connect():
                return jsonify([])
            data = request.get_json()
            purpose_name = data.get('purpose_name', '').strip()
            
            if not purpose_name:
                db_manager.close()
                return jsonify({'success': False, 'message': '用途名称不能为空'})
            
            success = db_manager.add_inbound_purpose(purpose_name, company_id)
            db_manager.close()
            
            if success:
                return jsonify({'success': True, 'message': '入库用途添加成功'})
            else:
                return jsonify({'success': False, 'message': '入库用途添加失败'})
        elif request.method == 'DELETE':
            # 删除入库用途 - 每次都重新连接
            if not db_manager.connect():
                return jsonify([])
            # 只从query string中获取id，因为DELETE请求通常不包含body
            purpose_id = request.args.get('id')
            if not purpose_id:
                db_manager.close()
                return jsonify({'success': False, 'message': '用途ID不能为空'})
            
            result = db_manager.delete_inbound_purpose(int(purpose_id))
            db_manager.close()
            
            if result[0]:
                return jsonify({'success': True, 'message': result[1]})
            else:
                return jsonify({'success': False, 'message': result[1]})
    
    elif request.method == 'GET':
        # 获取入库记录 - 确保连接有效
        if not db_manager.connect():
            return jsonify([])
        records = db_manager.get_inbound_records(query_company_id)
        purposes = db_manager.get_inbound_purposes(query_company_id)
        models = db_manager.get_all_models(query_company_id)
        operators = db_manager.get_all_operators(query_company_id)
        db_manager.close()
        return jsonify(records)
    
    elif request.method == 'POST':
        # 检查是否是批量入库请求
        if request.path.endswith('/batch'):
            # 批量添加入库记录
            data = request.get_json(silent=True) or {}
            inbound_id = data.get('inbound_id')
            purpose_id = data.get('purpose_id')
            inbound_date = data.get('inbound_date')
            items = data.get('items', [])
            notes = (data.get('notes') or '').strip()
            
            # 从session获取当前登录用户
            username = session.get('username', '未知用户')
            
            if not purpose_id:
                return jsonify({'success': False, 'message': '请选择入库用途'})
            if not items or not isinstance(items, list):
                return jsonify({'success': False, 'message': '批量数据不能为空'})
            if len(items) > MAX_BATCH_ITEMS:
                return jsonify({'success': False, 'message': f'单次批量操作最多支持{MAX_BATCH_ITEMS}项，请分批提交'})
            
            # 验证每个item的结构和数据
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    return jsonify({'success': False, 'message': f'第{i+1}条数据格式错误'})
                if not item.get('model_id'):
                    return jsonify({'success': False, 'message': f'第{i+1}条数据缺少设备型号'})
                if not item.get('quantity'):
                    return jsonify({'success': False, 'message': f'第{i+1}条数据缺少入库数量'})
                try:
                    quantity = int(item.get('quantity'))
                    if quantity <= 0:
                        return jsonify({'success': False, 'message': f'第{i+1}条数据的入库数量必须大于0'})
                except (ValueError, TypeError):
                    return jsonify({'success': False, 'message': f'第{i+1}条数据的入库数量必须是有效的整数'})
            
            # 验证入库类型和型号是否存在
            try:
                temp_conn = pymysql.connect(
                    host=db_manager.host,
                    user=db_manager.user,
                    password=db_manager.password,
                    database=db_manager.database
                )
                temp_cursor = temp_conn.cursor()
                temp_cursor.execute("SELECT id FROM inbound_types WHERE id = %s", (int(purpose_id),))
                if not temp_cursor.fetchone():
                    temp_cursor.close()
                    temp_conn.close()
                    return jsonify({'success': False, 'message': f'选择的入库类型不存在，请检查后重试'})
                
                # 验证每个设备型号是否存在
                for i, item in enumerate(items):
                    model_id = int(item.get('model_id'))
                    temp_cursor.execute("SELECT id FROM device_models WHERE id = %s", (model_id,))
                    if not temp_cursor.fetchone():
                        temp_cursor.close()
                        temp_conn.close()
                        return jsonify({'success': False, 'message': f'第{i+1}条数据的设备型号不存在，请检查后重试'})
                
                temp_cursor.close()
                temp_conn.close()
            except Exception as e:
                print(f"验证数据失败: {e}")
                traceback.print_exc()
                return jsonify({'success': False, 'message': f'验证数据失败: {str(e)}'})
            
            try:
                success = db_manager.add_batch_inbound_records(
                    int(purpose_id), username, items, notes, inbound_id, inbound_date,
                    data.get('warehouse_location_id'), data.get('store_id'), data.get('start_use_date'), company_id
                )
            except Exception as e:
                print(f"批量入库执行失败: {e}")
                traceback.print_exc()
                return jsonify({'success': False, 'message': f'批量入库失败: {str(e)}'})
            
            if success:
                return jsonify({'success': True, 'message': f'批量入库成功，共处理{len(items)}种设备'})
            else:
                return jsonify({'success': False, 'message': '批量入库失败：数据库操作返回失败，请检查服务器日志获取详细错误信息'})
        else:
            data = request.get_json()
            model_id = data.get('model_id')
            quantity = data.get('quantity')
            purpose_id = data.get('purpose_id')
            inbound_date = data.get('inbound_date')
            serial_number = data.get('serial_number', '').strip()
            unit_code = data.get('unit_code', '').strip()
            notes = data.get('notes', '').strip()
            warehouse_location_id = data.get('warehouse_location_id')
            store_id = data.get('store_id')
            start_use_date = data.get('start_use_date')
            
            username = session.get('username', '未知用户')
            
            if not model_id:
                return jsonify({'success': False, 'message': '请选择设备型号'})
            if not quantity or quantity <= 0:
                return jsonify({'success': False, 'message': '入库数量必须大于0'})
            if not purpose_id:
                return jsonify({'success': False, 'message': '请选择入库用途'})
            
            success = db_manager.add_inbound_record(
                int(model_id), int(quantity), int(purpose_id), username, 
                serial_number, unit_code, notes, inbound_date,
                warehouse_location_id, store_id, start_use_date, company_id
            )
            
            if success:
                return jsonify({'success': True, 'message': '入库记录添加成功'})
            else:
                return jsonify({'success': False, 'message': '入库记录添加失败'})

@app.route('/api/outbound', methods=['GET', 'POST'])
@app.route('/api/outbound/purpose', methods=['GET', 'POST', 'DELETE'])
@app.route('/api/outbound/batch', methods=['POST'])
@require_auth
def api_outbound():
    """出库管理API"""
    company_id = get_current_company_id()
    query_company_id = get_current_company_id(for_query=True)
    
    if request.path.endswith('/purpose'):
        if request.method == 'GET':
            # 获取出库用途列表 - 每次都重新连接
            if not db_manager.connect():
                return jsonify([])
            purposes = db_manager.get_outbound_purposes(query_company_id)
            db_manager.close()
            return jsonify(purposes)
        elif request.method == 'POST':
            # 添加出库用途 - 每次都重新连接
            if not db_manager.connect():
                return jsonify([])
            data = request.get_json()
            purpose_name = data.get('purpose_name', '').strip()
            
            if not purpose_name:
                db_manager.close()
                return jsonify({'success': False, 'message': '用途名称不能为空'})
            
            success = db_manager.add_outbound_purpose(purpose_name, company_id)
            db_manager.close()
            
            if success:
                return jsonify({'success': True, 'message': '出库用途添加成功'})
            else:
                return jsonify({'success': False, 'message': '出库用途添加失败'})
        elif request.method == 'DELETE':
            # 删除出库用途 - 每次都重新连接
            if not db_manager.connect():
                return jsonify([])
            # 只从query string中获取id，因为DELETE请求通常不包含body
            purpose_id = request.args.get('id')
            if not purpose_id:
                db_manager.close()
                return jsonify({'success': False, 'message': '用途ID不能为空'})
            
            success = db_manager.delete_outbound_purpose(int(purpose_id))
            db_manager.close()
            
            if success:
                return jsonify({'success': True, 'message': '出库用途删除成功'})
            else:
                return jsonify({'success': False, 'message': '出库用途删除失败'})
    
    elif request.method == 'GET':
        # 获取出库记录 - 确保连接有效
        if not db_manager.connect():
            return jsonify([])
        records = db_manager.get_outbound_records(query_company_id)
        purposes = db_manager.get_outbound_purposes(query_company_id)
        models = db_manager.get_all_models(query_company_id)
        operators = db_manager.get_all_operators(query_company_id)
        db_manager.close()
        return jsonify(records)

    elif request.method == 'POST':
        # 检查是否是批量出库请求
        if request.path.endswith('/batch'):
            # 批量添加出库记录
            data = request.get_json(silent=True) or {}
            outbound_id = data.get('outbound_id')
            purpose_id = data.get('purpose_id')
            outbound_date = data.get('outbound_date')
            items = data.get('items', [])
            notes = (data.get('notes') or '').strip()
            
            # 从session获取当前登录用户
            username = session.get('username', '未知用户')
            
            if not purpose_id:
                return jsonify({'success': False, 'message': '请选择出库用途'})
            if not items or not isinstance(items, list):
                return jsonify({'success': False, 'message': '批量数据不能为空'})
            if len(items) > MAX_BATCH_ITEMS:
                return jsonify({'success': False, 'message': f'单次批量操作最多支持{MAX_BATCH_ITEMS}项，请分批提交'})
            
            # 验证每个item的结构和数据
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    return jsonify({'success': False, 'message': f'第{i+1}条数据格式错误'})
                if not item.get('model_id'):
                    return jsonify({'success': False, 'message': f'第{i+1}条数据缺少设备型号'})
                try:
                    quantity = int(item.get('quantity'))
                    if quantity <= 0:
                        return jsonify({'success': False, 'message': f'第{i+1}条数据的出库数量必须大于0'})
                except (ValueError, TypeError):
                    return jsonify({'success': False, 'message': f'第{i+1}条数据的出库数量必须是有效的整数'})
            
            try:
                result = db_manager.add_batch_outbound_records(
                    int(purpose_id), username, items, notes, outbound_id, outbound_date,
                    data.get('warehouse_location_id'), data.get('store_id'), data.get('start_use_date'), company_id
                )
            except Exception as e:
                print(f"批量出库执行失败: {e}")
                traceback.print_exc()
                return jsonify({'success': False, 'message': f'批量出库失败: {str(e)}'})
            
            if isinstance(result, tuple) and len(result) >= 2:
                success, error_message = result
            else:
                # 向后兼容：如果返回的是布尔值
                success = result
                error_message = None
            
            if success:
                return jsonify({'success': True, 'message': f'批量出库成功，共处理{len(items)}种设备'})
            else:
                # 如果有具体的错误消息，使用它；否则使用默认消息
                if error_message:
                    return jsonify({'success': False, 'message': f'批量出库失败: {error_message}'})
                else:
                    return jsonify({'success': False, 'message': '批量出库失败，库存不足'})
        else:
            data = request.get_json()
            model_id = data.get('model_id')
            quantity = data.get('quantity')
            purpose_id = data.get('purpose_id')
            outbound_date = data.get('outbound_date')
            serial_number = data.get('serial_number', '').strip()
            unit_code = data.get('unit_code', '').strip()
            notes = data.get('notes', '').strip()
            warehouse_location_id = data.get('warehouse_location_id')
            store_id = data.get('store_id')
            start_use_date = data.get('start_use_date')
            work_order_number = data.get('work_order_number', '').strip()
            
            username = session.get('username', '未知用户')
            
            if not model_id:
                return jsonify({'success': False, 'message': '请选择设备型号'})
            if not quantity or quantity <= 0:
                return jsonify({'success': False, 'message': '出库数量必须大于0'})
            if not purpose_id:
                return jsonify({'success': False, 'message': '请选择出库用途'})
            
            success = db_manager.add_outbound_record(
                int(model_id), int(quantity), int(purpose_id), username,
                serial_number, unit_code, notes, outbound_date,
                warehouse_location_id, store_id, start_use_date, company_id, work_order_number
            )
            
            if success:
                return jsonify({'success': True, 'message': '出库记录添加成功'})
            else:
                return jsonify({'success': False, 'message': '出库记录添加失败，库存不足'})

# 页面路由
@app.route('/')
@login_required
def index():
    """首页"""
    companies = db_manager.get_all_companies()
    return render_template('index.html', companies=companies)

@app.route('/api/company/switch', methods=['POST'])
@require_auth
def api_switch_company():
    """切换当前公司"""
    data = request.get_json()
    company_id = data.get('company_id')

    if not company_id:
        return jsonify({'success': False, 'message': '请选择公司'})

    company = db_manager.get_company_by_id(company_id)
    if not company:
        return jsonify({'success': False, 'message': '公司不存在'})

    # 清除旧公司的 ITS Token 缓存
    old_company_id = session.get('company_id')
    if old_company_id and old_company_id != company_id:
        try:
            from its_service import clear_company_token_cache
            clear_company_token_cache(old_company_id)
        except ImportError:
            pass

    session['company_id'] = company['id']
    session['company_name'] = company['company_name']

    return jsonify({'success': True, 'message': '切换成功', 'company_name': company['company_name']})

@app.route('/api/companies', methods=['GET', 'POST'])
@require_auth
def api_companies():
    """公司管理API"""
    if not db_manager.connect():
        return jsonify([])
    
    if request.method == 'GET':
        companies = db_manager.get_all_companies()
        db_manager.close()
        # 脱敏：只回传占位符，不把（密文）密码发给前端，避免回显与泄露
        for c in companies or []:
            c['its_password'] = SECRET_PLACEHOLDER if c.get('its_password') else ''
        return jsonify(companies)
    
    elif request.method == 'POST':
        data = request.get_json()
        company_code = data.get('company_code', '').strip()
        company_name = data.get('company_name', '').strip()
        description = data.get('description', '').strip()
        its_url = data.get('its_url', '').strip()
        its_account = data.get('its_account', '').strip()
        its_password = data.get('its_password', '').strip()

        if not company_code or not company_name:
            db_manager.close()
            return jsonify({'success': False, 'message': '公司代码和名称不能为空'})

        if its_password == SECRET_PLACEHOLDER:      # 新建公司时占位符无意义
            its_password = ''
        if its_password and not secret_encryption_enabled():
            db_manager.close()
            return jsonify({'success': False,
                            'message': '服务器未配置 ITS_SECRET_KEY，无法保存 ITS 密码'})

        success = db_manager.add_company(company_code, company_name, description, its_url, its_account, its_password)
        db_manager.close()
        
        if success:
            return jsonify({'success': True, 'message': '添加公司成功'})
        else:
            return jsonify({'success': False, 'message': '添加公司失败，可能是公司代码已存在'})

@app.route('/api/companies/<int:company_id>', methods=['PUT', 'DELETE'])
@require_auth
def api_company_by_id(company_id):
    """公司管理API（单个）"""
    if not db_manager.connect():
        return jsonify([])
    
    if request.method == 'PUT':
        data = request.get_json()
        company_code = data.get('company_code', '').strip()
        company_name = data.get('company_name', '').strip()
        description = data.get('description', '').strip()
        its_url = data.get('its_url', '').strip()
        its_account = data.get('its_account', '').strip()
        its_password = data.get('its_password', '').strip()

        if not company_code or not company_name:
            db_manager.close()
            return jsonify({'success': False, 'message': '公司代码和名称不能为空'})

        # 留空或提交脱敏占位符 => 不修改原密码，避免把 ****** 写回数据库
        if its_password in ('', SECRET_PLACEHOLDER):
            its_password = None
        elif not secret_encryption_enabled():
            db_manager.close()
            return jsonify({'success': False,
                            'message': '服务器未配置 ITS_SECRET_KEY，无法保存 ITS 密码'})

        success = db_manager.update_company(company_id, company_code, company_name, description, its_url, its_account, its_password)
        db_manager.close()
        
        if success:
            return jsonify({'success': True, 'message': '更新公司成功'})
        else:
            return jsonify({'success': False, 'message': '更新公司失败'})
    
    elif request.method == 'DELETE':
        success, message = db_manager.delete_company(company_id)
        db_manager.close()
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'message': message})

# ==================== ITS 工单系统代理路由 ====================

@app.route('/api/its/workorders/<store_code>')
@require_auth
def api_its_workorders(store_code):
    """查询门店最近 ITS 工单

    自动登录 ITS 系统（含验证码 OCR），查询指定门店最近 2 条工单。
    """
    company_id = get_current_company_id()
    if not company_id:
        return jsonify({'success': False, 'message': '未选择公司'})

    company = db_manager.get_company_by_id(company_id)
    if not company:
        return jsonify({'success': False, 'message': '公司不存在'})

    its_url = company.get('its_url', '')
    its_account = company.get('its_account', '')
    try:
        its_password = decrypt_secret(company.get('its_password') or '')   # 库中为密文，此处解密后使用
    except Exception as e:
        app.logger.exception(f'ITS 密码解密失败: {e}')
        return jsonify({'success': False, 'message': f'ITS 密码解密失败: {e}'})

    if not its_url or not its_account:
        return jsonify({'success': False, 'message': '请先在设置中配置 ITS 系统地址和账号'})

    try:
        from its_service import ITSService
        service = ITSService(its_url, its_account, its_password, company_id)
        result = service.query_work_orders(store_code, page_size=2)

        if 'workorders' in result:
            return jsonify({'success': True, 'workorders': result['workorders']})
        else:
            # OCR 失败或登录失败，返回需要手动纠正的标记
            return jsonify({
                'success': False,
                'message': result.get('error', '查询失败'),
                'need_manual': result.get('need_manual', False)
            })
    except ImportError:
        return jsonify({'success': False, 'message': 'ITS 服务模块未安装'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'查询异常: {e}'})
    finally:
        try:
            service.close()      # 释放 requests.Session 占用的套接字，避免累积
        except Exception:
            pass


@app.route('/api/its/captcha')
@require_auth
def api_its_captcha():
    """获取 ITS 验证码图片（手动纠正场景）

    返回 base64 图片 + check_key，前端显示后由用户手动输入验证码。
    """
    company_id = get_current_company_id()
    if not company_id:
        return jsonify({'success': False, 'message': '未选择公司'})

    company = db_manager.get_company_by_id(company_id)
    if not company:
        return jsonify({'success': False, 'message': '公司不存在'})

    its_url = company.get('its_url', '')
    its_account = company.get('its_account', '')
    try:
        its_password = decrypt_secret(company.get('its_password') or '')   # 库中为密文，此处解密后使用
    except Exception as e:
        app.logger.exception(f'ITS 密码解密失败: {e}')
        return jsonify({'success': False, 'message': f'ITS 密码解密失败: {e}'})

    if not its_url:
        return jsonify({'success': False, 'message': '未配置 ITS 系统地址'})

    try:
        from its_service import ITSService
        service = ITSService(its_url, its_account, its_password, company_id)
        base64_data, check_key = service.fetch_captcha()
        return jsonify({
            'success': True,
            'image': f'data:image/jpeg;base64,{base64_data}',
            'check_key': check_key
        })
    except ImportError:
        return jsonify({'success': False, 'message': 'ITS 服务模块未安装'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取验证码失败: {e}'})
    finally:
        try:
            service.close()      # 释放 requests.Session 占用的套接字，避免累积
        except Exception:
            pass


@app.route('/api/its/login', methods=['POST'])
@require_auth
def api_its_login():
    """手动提交验证码登录 ITS（OCR 失败后的纠正入口）

    POST body: {"check_key": "...", "captcha": "用户手动输入的验证码"}
    """
    company_id = get_current_company_id()
    if not company_id:
        return jsonify({'success': False, 'message': '未选择公司'})

    company = db_manager.get_company_by_id(company_id)
    if not company:
        return jsonify({'success': False, 'message': '公司不存在'})

    its_url = company.get('its_url', '')
    its_account = company.get('its_account', '')
    try:
        its_password = decrypt_secret(company.get('its_password') or '')   # 库中为密文，此处解密后使用
    except Exception as e:
        app.logger.exception(f'ITS 密码解密失败: {e}')
        return jsonify({'success': False, 'message': f'ITS 密码解密失败: {e}'})

    data = request.get_json()
    captcha = data.get('captcha', '').strip()
    check_key = data.get('check_key', '').strip()

    if not captcha or not check_key:
        return jsonify({'success': False, 'message': '验证码和 check_key 不能为空'})

    try:
        from its_service import ITSService
        service = ITSService(its_url, its_account, its_password, company_id)
        result = service.login(captcha, check_key)

        if 'token' in result:
            return jsonify({'success': True, 'message': '登录成功'})
        else:
            return jsonify({'success': False, 'message': result.get('error', '登录失败')})
    except ImportError:
        return jsonify({'success': False, 'message': 'ITS 服务模块未安装'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'登录异常: {e}'})
    finally:
        try:
            service.close()      # 释放 requests.Session 占用的套接字，避免累积
        except Exception:
            pass

# ==================== ITS 代理路由结束 ====================

@app.route('/categories')
@login_required
def categories():
    """设备分类管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('categories.html', categories=[])
    
    categories = db_manager.get_all_categories(query_company_id)
    db_manager.close()
    return render_template('categories.html', categories=categories)

@app.route('/models')
@login_required
def models():
    """设备型号管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('models.html', models=[], categories=[])
    
    models = db_manager.get_all_models(query_company_id)
    categories = db_manager.get_all_categories(query_company_id)
    db_manager.close()
    return render_template('models.html', models=models, categories=categories)

@app.route('/units')
@login_required
def units():
    """设备编号管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('units.html', units=[], models=[])
    
    models = db_manager.get_all_models(company_id)
    units = db_manager.get_all_units(company_id)
    db_manager.close()
    return render_template('units.html', units=units, models=models)

@app.route('/api/search/models')
@require_auth
def api_search_models():
    """搜索设备型号API"""
    query_company_id = get_current_company_id(for_query=True)
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        models = db_manager.get_all_models(query_company_id)
        return jsonify(models)
    
    models = db_manager.search_models(keyword, query_company_id)
    return jsonify(models)

@app.route('/api/search/units')
@require_auth
def api_search_units():
    """搜索设备单元API"""
    query_company_id = get_current_company_id(for_query=True)
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        units = db_manager.get_all_device_units(query_company_id)
        return jsonify(units)
    
    units = db_manager.search_device_units(keyword, query_company_id)
    return jsonify(units)

@app.route('/api/search/stores')
@require_auth
def api_search_stores():
    """搜索店号店名API"""
    query_company_id = get_current_company_id(for_query=True)
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        stores = db_manager.get_all_stores(query_company_id)
        return jsonify(stores)
    
    stores = db_manager.search_stores(keyword, query_company_id)
    return jsonify(stores)

@app.route('/api/search/warehouse_locations')
@require_auth
def api_search_warehouse_locations():
    """搜索仓库位置API"""
    query_company_id = get_current_company_id(for_query=True)
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        locations = db_manager.get_all_warehouse_locations(query_company_id)
        return jsonify(locations)
    
    locations = db_manager.search_warehouse_locations(keyword, query_company_id)
    return jsonify(locations)

@app.route('/inventory')
@login_required
def inventory():
    """库存管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('inventory.html', inventory=[], models=[])
    
    models = db_manager.get_all_models(query_company_id)
    inventory = db_manager.get_inventory(query_company_id)
    db_manager.close()
    return render_template('inventory.html', inventory=inventory, models=models)

@app.route('/alerts')
@login_required
def alerts():
    """库存告警设置页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('alerts.html', alerts=[], models=[])
    
    models = db_manager.get_all_models(query_company_id)
    alerts = db_manager.get_stock_alerts(query_company_id)
    db_manager.close()
    return render_template('alerts.html', alerts=alerts, models=models)



@app.route('/batch-inbound')
@login_required
def batch_inbound():
    """批量入库页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return redirect(url_for('index'))
    
    models = db_manager.get_all_models(query_company_id)
    purposes = db_manager.get_inbound_purposes(query_company_id)
    records = db_manager.get_inbound_records(query_company_id)
    db_manager.close()
    
    return render_template('batch_inbound.html', models=models, purposes=purposes, records=records)

@app.route('/batch-outbound')
@login_required
def batch_outbound():
    """批量出库页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return redirect(url_for('index'))
    
    models = db_manager.get_all_models(query_company_id)
    purposes = db_manager.get_outbound_purposes(query_company_id)
    db_manager.close()
    
    return render_template('batch_outbound.html', models=models, purposes=purposes)

@app.route('/report')
@login_required
def report():
    """报表统计页面（支持日期范围查询与导出）"""
    query_company_id = get_current_company_id(for_query=True)

    record_type = (request.args.get('type') or 'all').lower()
    if record_type not in ('all', 'inbound', 'outbound'):
        record_type = 'all'

    start, end = db_manager._parse_date_range(request.args.get('start'), request.args.get('end'))
    summary = db_manager.get_operations_summary(start, end, query_company_id) or {}
    records = db_manager.get_operations_by_date_range(
        start, end, query_company_id, record_type, limit=500)

    return render_template(
        'report.html',
        start_date=start.strftime('%Y-%m-%d'),
        end_date=end.strftime('%Y-%m-%d'),
        record_type=record_type,
        summary=summary,
        records=records
    )

@app.route('/setup')
def setup():
    """数据库设置页面"""
    return render_template('setup.html')

@app.route('/api/setup', methods=['POST'])
def api_setup():
    """数据库设置API"""
    try:
        # 创建数据库（如果不存在）
        if not db_manager.connect():
            return jsonify([])
        
        # 创建表
        if not db_manager.create_tables():
            db_manager.close()
            return jsonify({'success': False, 'message': '创建数据库表失败'})
        
        # 初始化默认数据
        if not db_manager.init_default_data():
            db_manager.close()
            return jsonify({'success': False, 'message': '初始化默认数据失败'})
        
        db_manager.close()
        return jsonify({'success': True, 'message': '数据库设置成功'})
    except Exception as e:
        print(f"数据库设置失败: {e}")
        return jsonify({'success': False, 'message': f'数据库设置失败: {str(e)}'})

@app.route('/inbound')
@login_required
def inbound():
    """入库管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('inbound.html', records=[], purposes=[], models=[], operators=[])
    
    records = db_manager.get_inbound_records(query_company_id)
    purposes = db_manager.get_inbound_purposes(query_company_id)
    models = db_manager.get_all_models(query_company_id)
    operators = db_manager.get_all_operators(query_company_id)
    db_manager.close()
    return render_template('inbound.html', records=records, purposes=purposes, models=models, operators=operators)

@app.route('/outbound')
@login_required
def outbound():
    """出库管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('outbound.html', records=[], purposes=[], models=[], operators=[])
    
    records = db_manager.get_outbound_records(query_company_id)
    purposes = db_manager.get_outbound_purposes(query_company_id)
    models = db_manager.get_all_models(query_company_id)
    operators = db_manager.get_all_operators(query_company_id)
    db_manager.close()
    return render_template('outbound.html', records=records, purposes=purposes, models=models, operators=operators)

@app.route('/purposes')
@login_required
def purposes():
    """出入库用途管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('purposes.html', inbound_purposes=[], outbound_purposes=[])
    
    inbound_purposes = db_manager.get_inbound_purposes(query_company_id)
    outbound_purposes = db_manager.get_outbound_purposes(query_company_id)
    db_manager.close()
    return render_template('purposes.html', inbound_purposes=inbound_purposes, outbound_purposes=outbound_purposes)

@app.route('/operators')
@login_required
def operators():
    """操作人员管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('operators.html', operators=[])
    
    operators = db_manager.get_all_operators(query_company_id)
    db_manager.close()
    return render_template('operators.html', operators=operators)

@app.route('/warehouse_locations')
@login_required
def warehouse_locations():
    """仓库位置管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    locations = db_manager.get_all_warehouse_locations(query_company_id)
    return render_template('warehouse_locations.html', locations=locations)

@app.route('/api/warehouse_locations', methods=['GET', 'POST'])
@app.route('/api/warehouse_locations/<int:location_id>', methods=['PUT', 'DELETE'])
@require_auth
def api_warehouse_locations(location_id=None):
    """仓库位置管理API"""
    query_company_id = get_current_company_id(for_query=True)
    company_id = get_current_company_id()
    
    if request.method == 'GET':
        locations = db_manager.get_all_warehouse_locations(query_company_id)
        return jsonify(locations)
    elif request.method == 'POST':
        data = request.get_json()
        location_name = data.get('location_name', '').strip()
        location_code = data.get('location_code', '').strip()
        description = data.get('description', '').strip()
        
        if not location_name:
            return jsonify({'success': False, 'message': '仓库名称不能为空'})
        
        success = db_manager.add_warehouse_location(location_name, location_code, description, company_id)
        if success:
            return jsonify({'success': True, 'message': '仓库位置添加成功'})
        else:
            return jsonify({'success': False, 'message': '仓库位置添加失败，可能名称已存在'})
    elif request.method == 'PUT':
        data = request.get_json()
        location_name = data.get('location_name', '').strip()
        location_code = data.get('location_code', '').strip()
        description = data.get('description', '').strip()
        
        if not location_name:
            return jsonify({'success': False, 'message': '仓库名称不能为空'})
        
        success = db_manager.update_warehouse_location(location_id, location_name, location_code, description)
        if success:
            return jsonify({'success': True, 'message': '仓库位置更新成功'})
        else:
            return jsonify({'success': False, 'message': '仓库位置更新失败'})
    elif request.method == 'DELETE':
        success = db_manager.delete_warehouse_location(location_id)
        if success:
            return jsonify({'success': True, 'message': '仓库位置删除成功'})
        else:
            return jsonify({'success': False, 'message': '仓库位置删除失败'})

@app.route('/stores')
@login_required
def stores():
    """店号店名管理页面"""
    query_company_id = get_current_company_id(for_query=True)
    stores_list = db_manager.get_all_stores(query_company_id)
    return render_template('stores.html', stores=stores_list)

@app.route('/api/stores', methods=['GET', 'POST'])
@app.route('/api/stores/<int:store_id>', methods=['PUT', 'DELETE'])
@require_auth
def api_stores(store_id=None):
    """店号店名管理API"""
    query_company_id = get_current_company_id(for_query=True)
    company_id = get_current_company_id()
    
    if request.method == 'GET':
        stores_list = db_manager.get_all_stores(query_company_id)
        return jsonify(stores_list)
    elif request.method == 'POST':
        data = request.get_json()
        store_code = data.get('store_code', '').strip()
        store_name = data.get('store_name', '').strip()
        contact_person = data.get('contact_person', '').strip()
        phone = data.get('phone', '').strip()
        address = data.get('address', '').strip()
        description = data.get('description', '').strip()
        
        if not store_code or not store_name:
            return jsonify({'success': False, 'message': '店号和店名不能为空'})
        
        success = db_manager.add_store(store_code, store_name, contact_person, phone, address, description, company_id)
        if success:
            return jsonify({'success': True, 'message': '店号店名添加成功'})
        else:
            return jsonify({'success': False, 'message': '店号店名添加失败，可能店号已存在'})
    elif request.method == 'PUT':
        data = request.get_json()
        store_code = data.get('store_code', '').strip()
        store_name = data.get('store_name', '').strip()
        contact_person = data.get('contact_person', '').strip()
        phone = data.get('phone', '').strip()
        address = data.get('address', '').strip()
        description = data.get('description', '').strip()
        
        if not store_code or not store_name:
            return jsonify({'success': False, 'message': '店号和店名不能为空'})
        
        success = db_manager.update_store(store_id, store_code, store_name, contact_person, phone, address, description)
        if success:
            return jsonify({'success': True, 'message': '店号店名更新成功'})
        else:
            return jsonify({'success': False, 'message': '店号店名更新失败'})
    elif request.method == 'DELETE':
        try:
            print(f"尝试删除店号店名，ID: {store_id}")
            result = db_manager.delete_store(store_id)
            print(f"删除结果: {result}")
            if isinstance(result, tuple):
                success, message = result
            else:
                success = result
                message = "删除成功" if result else "删除失败"
            if success:
                return jsonify({'success': True, 'message': message})
            else:
                return jsonify({'success': False, 'message': message})
        except Exception as e:
            print(f"删除店号店名时发生错误: {e}")
            return jsonify({'success': False, 'message': f'删除失败: {str(e)}'})

@app.route('/api/stores/batch_import', methods=['POST'])
@require_auth
def api_stores_batch_import():
    """店号店名批量导入API"""
    company_id = get_current_company_id()
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有上传文件'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'})
    
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'success': False, 'message': '请上传Excel文件(.xlsx或.xls)'})
    
    try:
        import pandas as pd
        df = pd.read_excel(file)
        
        required_columns = ['店号', '店名']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            return jsonify({'success': False, 'message': f'Excel文件缺少必需列: {", ".join(missing_columns)}'})
        
        stores_data = []
        for _, row in df.iterrows():
            store_data = {
                'store_code': str(row['店号']).strip() if pd.notna(row['店号']) else '',
                'store_name': str(row['店名']).strip() if pd.notna(row['店名']) else '',
                'contact_person': str(row.get('联系人', '')).strip() if pd.notna(row.get('联系人', '')) else '',
                'phone': str(row.get('电话', '')).strip() if pd.notna(row.get('电话', '')) else '',
                'address': str(row.get('地址', '')).strip() if pd.notna(row.get('地址', '')) else '',
                'description': str(row.get('备注', '')).strip() if pd.notna(row.get('备注', '')) else ''
            }
            if store_data['store_code'] and store_data['store_name']:
                stores_data.append(store_data)
        
        if not stores_data:
            return jsonify({'success': False, 'message': 'Excel文件中没有有效数据'})
        
        success, message = db_manager.batch_import_stores(stores_data, company_id)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'message': f'导入失败: {str(e)}'})


@app.route('/api/settings', methods=['GET', 'POST'])
@require_auth
def api_settings():
    """系统设置API"""
    if request.method == 'GET':
        settings = db_manager.get_system_settings()
        return jsonify(settings)
    elif request.method == 'POST':
        data = request.get_json()
        timezone = data.get('timezone')
        language = data.get('language')
        
        if not timezone or not language:
            return jsonify({'success': False, 'message': '时区和语言不能为空'})
        
        success = db_manager.update_system_settings(timezone, language)
        if success:
            return jsonify({'success': True, 'message': '设置更新成功'})
        else:
            return jsonify({'success': False, 'message': '设置更新失败'})


@app.route('/api/reset_data', methods=['POST'])
@require_auth
def api_reset_data():
    """数据重置API"""
    data = request.get_json()
    reset_type = data.get('reset_type', 'all')
    confirm = data.get('confirm', False)
    company_id = data.get('company_id') or get_current_company_id()
    
    if not confirm:
        return jsonify({'success': False, 'message': '请确认重置操作'})
    
    success, message = db_manager.reset_all_data(reset_type, company_id)
    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'success': False, 'message': f'重置失败: {message}'})


@app.route('/api/backup/download', methods=['GET'])
@require_auth
def download_backup():
    """创建并下载系统备份（MySQL + KDD快递单数据库）"""
    import tempfile
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = tempfile.mkdtemp()
    try:
        mysql_dump_path = os.path.join(backup_dir, 'mysql_device_inventory.sql')
        if not export_mysql_to_file(mysql_dump_path):
            return jsonify({'success': False, 'message': 'MySQL数据库导出失败'})

        if os.path.exists(EXPRESS_DB_PATH):
            shutil.copy2(EXPRESS_DB_PATH, os.path.join(backup_dir, 'express.db'))

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_name in os.listdir(backup_dir):
                file_path = os.path.join(backup_dir, file_name)
                zf.write(file_path, file_name)

        zip_buffer.seek(0)
        response = make_response(zip_buffer.getvalue())
        response.headers['Content-Type'] = 'application/zip'
        response.headers['Content-Disposition'] = f'attachment; filename=backup_{timestamp}.zip'
        return response
    except Exception as e:
        return jsonify({'success': False, 'message': f'备份失败: {str(e)}'})
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


@app.route('/settings')
@login_required
def settings():
    """系统设置页面"""
    settings = db_manager.get_system_settings()
    return render_template('settings.html', settings=settings)

@app.route('/users')
@login_required
def users():
    """用户管理页面"""
    if not db_manager.connect():
        flash('数据库连接失败', 'error')
        return render_template('users.html', users=[])
    
    # 获取所有用户信息
    users = db_manager.get_all_users()
    db_manager.close()
    return render_template('users.html', users=users)

@app.route('/api/users', methods=['GET', 'POST'])
@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@require_auth
def api_users(user_id=None):
    """用户管理API"""
    if request.method == 'GET':
        # 获取所有用户信息
        users = db_manager.get_all_users()
        return jsonify(users)
    elif request.method == 'POST':
        # 添加新用户
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', 'user')
        
        # 参数验证
        if not username or not password:
            return jsonify({'success': False, 'message': '用户名和密码不能为空'})
        
        # 添加用户
        success = db_manager.add_user(username, password, role)
        if success:
            return jsonify({'success': True, 'message': '用户添加成功'})
        else:
            return jsonify({'success': False, 'message': '用户添加失败，可能是用户名已存在'})
    elif request.method == 'DELETE' and user_id:
        # 删除用户
        success = db_manager.delete_user(user_id)
        if success:
            return jsonify({'success': True, 'message': '用户删除成功'})
        else:
            return jsonify({'success': False, 'message': '用户删除失败'})
    
    return jsonify({'success': False, 'message': '无效的请求'})

@app.route('/api/users/<int:user_id>/toggle-status', methods=['POST'])
@require_auth
def api_toggle_user_status(user_id):
    """切换用户状态API"""
    success, new_status = db_manager.toggle_user_status(user_id)
    if success:
        status_text = '启用' if new_status else '禁用'
        return jsonify({'success': True, 'message': f'用户已{status_text}', 'is_active': new_status})
    else:
        return jsonify({'success': False, 'message': '操作失败'})

@app.route('/api/users/change-password', methods=['POST'])
@require_auth
def api_change_password():
    """修改用户密码API"""
    data = request.get_json()
    user_id = data.get('user_id')
    password = data.get('password')
    
    if not user_id or not password:
        return jsonify({'success': False, 'message': '用户ID和密码不能为空'})
    
    if len(password) < 6:
        return jsonify({'success': False, 'message': '密码长度不能少于6位'})
    
    success = db_manager.change_user_password(user_id, password)
    if success:
        return jsonify({'success': True, 'message': '密码修改成功'})
    else:
        return jsonify({'success': False, 'message': '密码修改失败'})

@app.route('/api/permissions', methods=['GET'])
@require_auth
def api_get_permissions():
    """获取所有权限API"""
    permissions = db_manager.get_all_permissions()
    return jsonify(permissions)

@app.route('/api/users/<int:user_id>/permissions', methods=['GET', 'POST'])
@require_auth
def api_user_permissions(user_id):
    """用户权限管理API"""
    if request.method == 'GET':
        permissions = db_manager.get_user_permissions(user_id)
        return jsonify(permissions)
    elif request.method == 'POST':
        data = request.get_json()
        permission_ids = data.get('permission_ids', [])
        
        success = db_manager.update_user_permissions(user_id, permission_ids)
        if success:
            return jsonify({'success': True, 'message': '权限更新成功'})
        else:
            return jsonify({'success': False, 'message': '权限更新失败'})

@app.route('/api/users/<int:user_id>/companies', methods=['GET', 'POST'])
@require_auth
def api_user_companies(user_id):
    """用户公司权限管理API"""
    if request.method == 'GET':
        companies = db_manager.get_user_companies(user_id)
        return jsonify(companies)
    elif request.method == 'POST':
        data = request.get_json()
        company_ids = data.get('company_ids', [])
        
        success = db_manager.update_user_companies(user_id, company_ids)
        if success:
            return jsonify({'success': True, 'message': '公司权限更新成功'})
        else:
            return jsonify({'success': False, 'message': '公司权限更新失败'})

# 用户认证路由
@app.route('/login', methods=['GET', 'POST'])
def login():
    """用户登录"""
    error_type = request.args.get('error')
    if error_type == 'db_init':
        flash('数据库连接失败，请先运行数据库初始化脚本！', 'error')

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        company_id = request.form.get('company_id')
        if company_id:
            company_id = int(company_id)
        
        print(f"登录请求: username={username}, company_id={company_id}")
        
        # 先检查数据库是否可用
        conn = db_manager.get_connection()
        if not conn:
            flash('数据库连接失败，请确保MySQL服务已启动并运行了 init_database.py！', 'error')
            return render_template('login.html', companies=[])
        conn.close()
        
        # 检查是否有用户存在
        try:
            conn_check = db_manager.get_connection()
            cursor = conn_check.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM users")
            user_count = cursor.fetchone()['cnt']
            cursor.close()
            conn_check.close()
            
            if user_count == 0:
                flash('系统中没有用户账号，请先运行 init_database.py 初始化数据库后再试！', 'error')
                companies = db_manager.get_all_companies()
                return render_template('login.html', companies=companies)
        except Exception as e:
            print(f"检查用户表失败: {e}")
        
        user = db_manager.verify_user_password(username, password, company_id)
        if user:
            print(f"登录成功: user={user}")
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['company_id'] = user.get('company_id')
            session['company_name'] = user.get('company_name', '未知公司')
            print(f"设置session: {dict(session)}")
            flash('登录成功！', 'success')
            return redirect(url_for('index'))
        else:
            print(f"登录失败: username={username}")
            flash('用户名或密码错误，或没有公司访问权限！', 'error')
    
    companies = db_manager.get_all_companies()
    
    if not companies:
        db_manager.add_company('HQ', '总公司', '系统默认总公司')
        companies = db_manager.get_all_companies()
    
    return render_template('login.html', companies=companies)

@app.route('/api/public/companies')
def api_public_companies():
    """公开获取公司列表API（无需登录）

    安全要求：该接口无鉴权，任何人均可访问，因此只返回登录选择所需的最小字段，
    严禁返回 its_url / its_account / its_password 等 ITS 凭据（此前存在明文泄露）。
    可用性要求：数据库不可用时返回 503，而不是伪装成“空列表”，避免故障被静默掩盖
    （APP 端此前只会显示“服务器未返回任何公司信息”，无法区分故障与无数据）。
    """
    companies = db_manager.get_all_companies()
    if not companies:
        # 区分“数据库不可用”与“确实没有数据”
        probe = db_manager.get_connection()
        if probe is None:
            app.logger.error("api_public_companies: 数据库不可用，返回 503")
            return jsonify({'success': False, 'message': '服务暂时不可用，请稍后重试'}), 503
        try:
            probe.close()
        except Exception:
            pass

    return jsonify([
        {
            'id': c.get('id'),
            'company_code': c.get('company_code'),
            'company_name': c.get('company_name'),
            'description': c.get('description') or '',
            'is_active': bool(c.get('is_active', True)),
        }
        for c in companies
    ])

@app.route('/healthz')
def healthz():
    """健康检查端点：同时探测数据库连通性，供监控/探活使用

    返回 200 {"ok": true} 表示服务与数据库均正常；返回 503 表示依赖不可用。
    """
    conn = db_manager.get_connection()
    if conn is None:
        app.logger.error("healthz: 数据库不可用")
        return jsonify({'ok': False, 'db': False}), 503
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return jsonify({'ok': True, 'db': True})
    except Exception as e:
        app.logger.exception(f"healthz: 数据库探测失败: {e}")
        return jsonify({'ok': False, 'db': False}), 503
    finally:
        try:
            conn.close()
        except Exception:
            pass

@app.route('/logout')
def logout():
    """用户登出"""
    session.clear()
    flash('已成功登出！', 'success')
    return redirect(url_for('login'))

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """API JWT token登录接口"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据为空'}), 400
    
    username = data.get('username')
    password = data.get('password')
    company_id = data.get('company_id')
    
    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400
    
    if company_id:
        company_id = int(company_id)
    
    user = db_manager.verify_user_password(username, password, company_id)
    if not user:
        return jsonify({'error': '用户名或密码错误'}), 401
    
    identity = json.dumps({
        'user_id': user['id'],
        'username': user['username'],
        'role': user['role'],
        'company_id': user.get('company_id')
    })
    access_token = create_access_token(identity=identity)
    
    return jsonify({
        'token': access_token,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'role': user['role'],
            'company_id': user.get('company_id'),
            'company_name': user.get('company_name', '未知公司')
        }
    })

@app.route('/api/auth/logout', methods=['POST'])
@jwt_required()
def api_logout():
    return jsonify({'success': True})


# 注册快递单识别系统蓝图
app.register_blueprint(express_bp)

if __name__ == '__main__':
    # 初始化数据库
    if db_manager.connect():
        db_manager.create_tables()
        db_manager.init_default_data()
        db_manager.close()
    
    app.run(debug=False, host='0.0.0.0', port=5050)