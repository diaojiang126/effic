# -*- coding: utf-8 -*-
"""
ITS (Issue Ticket System) 工单系统代理服务

封装 ITS 系统的登录（含验证码 OCR）、工单查询、Token 缓存等能力。
后端代理模式：前端不直接访问 ITS，所有请求经由 Flask 路由转发至此模块。
"""
import time
import threading
import base64
import io
import os
import shutil
import requests

try:
    import cv2
    import numpy as np
    from PIL import Image
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import pytesseract
    from pytesseract import TesseractNotFoundError
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False
    TesseractNotFoundError = Exception


# 进程级 Token 缓存: {company_id: {'token': str, 'expires_at': float}}
_token_cache = {}
_cache_lock = threading.Lock()

# Token 有效期 25 分钟（JeecgBoot 默认 30 分钟，留 5 分钟余量）
TOKEN_TTL = 1500

TESSERACT_MISSING_MSG = (
    '服务器未安装 Tesseract-OCR，验证码自动识别不可用。'
    '请手动输入验证码，或联系管理员安装 Tesseract-OCR（含 chi_sim 语言包）并加入系统 PATH。'
)

# Tesseract 可能的安装路径，用于 PATH 未生效时的兜底查找
_TESSERACT_CANDIDATE_PATHS = [
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    r'D:\Program Files\Tesseract-OCR\tesseract.exe',
    # Linux: apt install tesseract-ocr
    '/usr/bin/tesseract',
    '/usr/local/bin/tesseract',
    '/snap/bin/tesseract',
]


def _find_tesseract_binary():
    """在 PATH 中或常见安装路径中查找 tesseract 可执行文件，返回完整路径或 None"""
    # 1. 优先使用环境变量 TESSERACT_CMD 指定的路径
    env_path = os.environ.get('TESSERACT_CMD')
    if env_path and os.path.isfile(env_path):
        return env_path
    # 2. 在 PATH 中查找
    which = shutil.which('tesseract')
    if which:
        return which
    # 3. 兜底：检查常见安装路径（用于 PATH 未刷新但已安装的情况）
    for p in _TESSERACT_CANDIDATE_PATHS:
        if os.path.isfile(p):
            return p
    return None


def check_tesseract_available():
    """检测 tesseract 可执行文件是否真正可用

    会先在 PATH 中查找，找不到时兜底检查常见安装路径，
    避免 Flask 服务进程在修改 PATH 前启动导致检测失败。
    """
    if not HAS_TESSERACT:
        return False, TESSERACT_MISSING_MSG
    binary = _find_tesseract_binary()
    if not binary:
        return False, TESSERACT_MISSING_MSG
    # 如果 tesseract 不在 PATH 但兜底找到了，设置 pytesseract 使用该路径
    if not shutil.which('tesseract'):
        pytesseract.pytesseract.tesseract_cmd = binary
    try:
        pytesseract.get_tesseract_version()
        return True, ''
    except TesseractNotFoundError:
        return False, TESSERACT_MISSING_MSG
    except Exception as e:
        return False, f'Tesseract 调用异常: {e}'


class ITSService:
    """ITS 工单系统代理"""

    def __init__(self, its_url, account, password, company_id=None):
        self.base_url = its_url.rstrip('/')
        self.account = account
        self.password = password
        self.company_id = company_id
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json;charset=UTF-8',
            'Accept': 'application/json, text/plain, */*',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def close(self):
        """关闭底层 HTTP 会话，释放连接池占用的套接字。

        requests.Session 会保持 keep-alive 连接，若每次请求都新建而不关闭，
        长期运行会累积套接字（生产环境曾因此类资源累积触发过套接字资源耗尽），
        故每次用完必须显式调用 close()。
        """
        try:
            self._session.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _get_timestamp():
        """生成毫秒级时间戳"""
        return str(int(time.time() * 1000))

    def _clear_cache(self):
        """清除当前公司的 token 缓存"""
        with _cache_lock:
            _token_cache.pop(self.company_id, None)

    # ------------------------------------------------------------------
    # 验证码
    # ------------------------------------------------------------------

    def fetch_captcha(self):
        """获取验证码图片

        GET /its-api/sys/randomImage/{checkKey}?_t={ts}
        ITS 返回 JSON: {"success":true, "result":"data:image/jpg;base64,..."}

        Returns:
            tuple: (image_base64, check_key)
            image_base64 不含 data:image 前缀
        """
        check_key = self._get_timestamp()
        url = f'{self.base_url}/its-api/sys/randomImage/{check_key}'
        params = {'_t': check_key}
        resp = self._session.get(url, params=params, timeout=10, verify=False)
        resp.raise_for_status()

        # ITS 返回 JSON 格式
        data = resp.json()
        result = data.get('result', '')

        # result 可能是 "data:image/jpg;base64,xxxx" 格式
        if result.startswith('data:image'):
            base64_data = result.split(',', 1)[1] if ',' in result else result
        else:
            base64_data = result

        return base64_data, check_key

    def ocr_captcha(self, base64_data):
        """用本地 tesseract 识别验证码

        ITS 验证码特征：4位混合大小写字母+数字，橙金色渐变字符+灰色噪点背景。
        预处理策略：颜色过滤提取橙色字符 → 灰度 → 放大4x → 二值化 → OCR。

        Args:
            base64_data: 不含 data:image 前缀的 base64 字符串

        Returns:
            str: 识别出的验证码文本
        """
        if not HAS_TESSERACT:
            raise RuntimeError(TESSERACT_MISSING_MSG)

        # 确保 tesseract_cmd 已设置（兜底路径，应对 PATH 未生效）
        if not getattr(pytesseract.pytesseract, 'tesseract_cmd', '') or pytesseract.pytesseract.tesseract_cmd == 'tesseract':
            binary = _find_tesseract_binary()
            if binary:
                pytesseract.pytesseract.tesseract_cmd = binary
            else:
                raise RuntimeError(TESSERACT_MISSING_MSG)

        # 解码 base64 -> PIL Image
        img_bytes = base64.b64decode(base64_data)
        img = Image.open(io.BytesIO(img_bytes))

        if HAS_CV2:
            cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR)

            # 颜色过滤：提取橙金色字符（ITS 验证码字符为橙/金色渐变）
            hsv = cv2.cvtColor(cv_img, cv2.COLOR_BGR2HSV)
            # 橙金色范围（H: 15-45, S: 50-255, V: 100-255）
            lower = np.array([10, 40, 80])
            upper = np.array([50, 255, 255])
            mask = cv2.inRange(hsv, lower, upper)

            # 如果颜色过滤结果太少（可能不是橙金色验证码），回退到灰度
            if cv2.countNonZero(mask) < 100:
                gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
            else:
                # 将 mask 转为灰度图（字符为白色，背景为黑色）
                gray = mask

            # 放大 4 倍
            h, w = gray.shape[:2]
            gray = cv2.resize(gray, (w * 4, h * 4), interpolation=cv2.INTER_CUBIC)

            # 去噪
            gray = cv2.medianBlur(gray, 3)

            # Otsu 二值化
            _, gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # 如果颜色过滤后是黑底白字，需要反转为白底黑字（tesseract 习惯黑字白底）
            if gray[0, 0] == 255:
                gray = cv2.bitwise_not(gray)

            img = Image.fromarray(gray)

        # 尝试多种 psm 配置，取最优结果
        configs = [
            '--psm 7 -c tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ',
            '--psm 8 -c tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ',
            '--psm 13 -c tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ',
        ]

        best_result = ''
        for config in configs:
            try:
                result = pytesseract.image_to_string(img, config=config)
            except TesseractNotFoundError:
                raise RuntimeError(TESSERACT_MISSING_MSG)
            result = result.strip().replace(' ', '').replace('\n', '').replace('\r', '')
            # 取 4-5 位的结果（ITS 验证码通常 4 位）
            if 4 <= len(result) <= 5:
                if len(result) > len(best_result):
                    best_result = result
            elif len(result) > 0 and not best_result:
                best_result = result

        return best_result

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------

    def login(self, captcha, check_key):
        """登录 ITS 系统

        POST /its-api/sys/login
        Body: {"username": "...", "password": "...", "captcha": "...", "checkKey": "..."}

        Returns:
            dict: {'token': str, 'user_info': dict} 成功时
            dict: {'error': str} 失败时
        """
        url = f'{self.base_url}/its-api/sys/login'
        payload = {
            'username': self.account,
            'password': self.password,
            'captcha': captcha,
            'checkKey': check_key
        }
        resp = self._session.post(url, json=payload, timeout=10, verify=False)

        if resp.status_code != 200:
            return {'error': f'HTTP {resp.status_code}'}

        data = resp.json()
        if data.get('success') or data.get('code') == 200 or data.get('code') == 0:
            token = data.get('result', {}).get('token', '')
            if token:
                # 缓存 token
                with _cache_lock:
                    _token_cache[self.company_id] = {
                        'token': token,
                        'expires_at': time.time() + TOKEN_TTL
                    }
                return {'token': token, 'user_info': data.get('result', {})}
            return {'error': '登录成功但未返回 token'}
        else:
            msg = data.get('message') or data.get('msg') or '登录失败'
            return {'error': msg}

    def get_valid_token(self, max_retries=3):
        """获取有效 Token（带缓存 + 自动重试）

        1. 检查缓存中是否有有效 token
        2. 无效则：取验证码 → OCR → 登录，失败重试
        3. 最多重试 max_retries 次

        Returns:
            dict: {'token': str} 成功
            dict: {'error': str, 'need_manual': True} 失败，需手动纠正
        """
        # 1. 检查缓存
        with _cache_lock:
            cached = _token_cache.get(self.company_id)
            if cached and cached['expires_at'] > time.time():
                return {'token': cached['token']}

        # 2. 防并发重复登录
        with _cache_lock:
            # 双重检查：可能在等锁期间其他线程已登录成功
            cached = _token_cache.get(self.company_id)
            if cached and cached['expires_at'] > time.time():
                return {'token': cached['token']}

        # 3. 预检 tesseract 是否可用，不可用则直接返回明确错误，不再空转重试
        ok, err_msg = check_tesseract_available()
        if not ok:
            return {'error': err_msg, 'need_manual': True}

        # 4. 循环重试
        last_error = ''
        for i in range(max_retries):
            try:
                base64_data, check_key = self.fetch_captcha()
                captcha = self.ocr_captcha(base64_data)
                if not captcha:
                    last_error = '验证码识别为空，请手动输入'
                    continue

                result = self.login(captcha, check_key)
                if 'token' in result:
                    return {'token': result['token']}
                last_error = result.get('error', '登录失败')
                # 验证码错误会提示"验证码错误"，继续重试
            except RuntimeError as e:
                # tesseract 未安装等致命错误，直接返回不再重试
                return {'error': str(e), 'need_manual': True}
            except Exception as e:
                last_error = f'登录异常: {e}'

        # 所有重试都失败
        return {'error': last_error, 'need_manual': True}

    # ------------------------------------------------------------------
    # 工单查询
    # ------------------------------------------------------------------

    def query_work_orders(self, store_code, page_size=2):
        """查询门店最近工单

        GET /its-api/biz/itsBizJob/list?storecode={store_code}&pageSize=2&column=createTime&order=desc
        注意：过滤参数为 storecode（门店编号，数字格式如 316967），makercode 会被 ITS 忽略。

        Returns:
            dict: {'workorders': [...]} 成功
            dict: {'error': str, 'need_manual': bool} 失败
        """
        # 获取 token
        token_result = self.get_valid_token()
        token = token_result.get('token')
        if not token:
            return token_result  # 返回错误信息

        url = f'{self.base_url}/its-api/biz/itsBizJob/list'
        params = {
            '_t': self._get_timestamp(),
            'storecode': store_code,
            'column': 'createTime',
            'order': 'desc',
            'field': 'id',
            'pageNo': 1,
            'pageSize': page_size
        }
        headers = {'X-Access-Token': token}

        resp = self._session.get(url, params=params, headers=headers, timeout=10, verify=False)

        if resp.status_code == 401:
            # Token 过期，清缓存重试一次
            self._clear_cache()
            token_result = self.get_valid_token(max_retries=1)
            token = token_result.get('token')
            if not token:
                return token_result
            resp = self._session.get(url, params=params, headers={'X-Access-Token': token}, timeout=10, verify=False)

        if resp.status_code != 200:
            return {'error': f'HTTP {resp.status_code}'}

        data = resp.json()
        if not (data.get('success') or data.get('code') == 200 or data.get('code') == 0):
            return {'error': data.get('message') or '查询失败'}

        # 提取工单列表
        result = data.get('result', {})
        records = result.get('records', []) if isinstance(result, dict) else result

        # 映射工单字段
        workorders = []
        for record in records:
            workorders.append(self._map_work_order(record))

        return {'workorders': workorders}

    @staticmethod
    def _map_work_order(record):
        """映射 ITS 工单字段为标准格式

        ITS API 返回字段：
        - jobcode: 工单编号
        - storecode_dictText: 门店名称（含店号）
        - createTime: 创建时间
        - depict: 问题描述
        """
        return {
            'id': record.get('id', ''),
            'work_order_number': record.get('jobcode') or record.get('jobCode') or '',
            'store_name': record.get('storecode_dictText') or record.get('storename') or record.get('storeName') or '',
            'create_time': record.get('createTime') or '',
            'description': record.get('depict') or record.get('description') or record.get('content') or '',
        }

    # ------------------------------------------------------------------
    # 登出
    # ------------------------------------------------------------------

    def logout(self):
        """登出 ITS 系统，清除缓存"""
        with _cache_lock:
            cached = _token_cache.get(self.company_id)
        if cached:
            try:
                url = f'{self.base_url}/its-api/sys/logout'
                self._session.post(url, headers={'X-Access-Token': cached['token']}, timeout=5, verify=False)
            except Exception:
                pass
        self._clear_cache()


def clear_company_token_cache(company_id):
    """清除指定公司的 ITS Token 缓存（公司切换时调用）"""
    with _cache_lock:
        _token_cache.pop(company_id, None)


# 禁用 InsecureRequestWarning（ITS 可能用自签名证书）
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
