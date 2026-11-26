import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
EXPORT_FOLDER = os.path.join(BASE_DIR, 'exports')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'gif'}
# 放宽到 20MB：原图不做有损压缩，允许直接上传手机高像素原片
MAX_CONTENT_LENGTH = 20 * 1024 * 1024

DATABASE_PATH = os.path.join(BASE_DIR, 'express.db')

# 百度 OCR 凭据：必须通过环境变量配置，不再提供代码内默认值。
# IIS 部署在 web.config 的 <appSettings> 中配置；主站通过项目根目录 .env 注入。
BAIDU_AIP_APP_ID    = os.environ.get('BAIDU_AIP_APP_ID', '')
BAIDU_AIP_API_KEY   = os.environ.get('BAIDU_AIP_API_KEY', '')
BAIDU_AIP_SECRET_KEY = os.environ.get('BAIDU_AIP_SECRET_KEY', '')

_baidu_configured = all([BAIDU_AIP_APP_ID, BAIDU_AIP_API_KEY, BAIDU_AIP_SECRET_KEY])
if not _baidu_configured:
    print('[config] 提示：百度 OCR 凭据未配置（BAIDU_AIP_APP_ID / BAIDU_AIP_API_KEY / BAIDU_AIP_SECRET_KEY），'
          '快递单 OCR 将不可用。请在 .env 或 web.config 中配置后重启应用。')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)