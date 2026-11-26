# ITS 运维系统集成 · 技术说明与定制指南

> 本系统通过 `its_service.py` 代理对接 ITS（Issue Ticket System）工单系统，
> 当前实现基于 **JeecgBoot 后台框架**的 ITS 标准接口协议。
> 如需对接其他工单系统（如自研 ITS、Zabbix、ServiceNow、禅道等），
> 本指南说明每一处需要修改的位置和方法。

---

## 一、整体架构

```
前端浏览器 / Android APP
          │
          │  HTTP (JSON)
          ▼
   app.py 路由层（3 个端点）
    │                │                │
    ▼                ▼                ▼
/api/its/workorders  /api/its/captcha  /api/its/login
          │                │                │
          └────────────────┼────────────────┘
                           │
                           ▼
                  its_service.py
                  ITSService 类
                           │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
  fetch_captcha()    login() / ocr_captcha()  query_work_orders()
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │  HTTP (requests.Session)
                          ▼
              ITS 工单系统 API
              (JeecgBoot 协议)
```

**为什么用代理？**
- 前端不直接访问 ITS，避免跨域和凭据暴露
- Token 缓存由后端统一维护（进程级缓存，25 分钟 TTL）
- 验证码 OCR 在服务端完成，前端无感

---

## 二、凭据存储

### 2.1 companies 表字段

```sql
-- 每次公司记录独立持有一套 ITS 凭据（支持多公司多 ITS 实例）
ALTER TABLE companies ADD COLUMN its_url VARCHAR(255);    -- ITS 根地址
ALTER TABLE companies ADD COLUMN its_account VARCHAR(100); -- ITS 登录账号
ALTER TABLE companies ADD COLUMN its_password VARCHAR(255); -- ITS 登录密码（加密存储）
```

| 字段 | 示例值 | 说明 |
|------|--------|------|
| `its_url` | `https://its.example.com` | **根地址**，不要带末尾 `/`，不要带路径 |
| `its_account` | `admin` | ITS 系统的登录用户名 |
| `its_password` | （加密密文） | 存储时经 `encrypt_secret()` 加密，读取时 `decrypt_secret()` 解密 |

### 2.2 加密机制

```python
# crypto_util.py
encrypt_secret(plaintext)   # AES-GCM 加密 + base64
decrypt_secret(ciphertext)  # 解密还原
secret_encryption_enabled() # 检查环境变量 ITS_SECRET_KEY 是否存在
```

**必须在 `.env` 中配置**：
```ini
ITS_SECRET_KEY=用 python -c "import secrets; print(secrets.token_urlsafe(32))" 生成
```

如果此变量缺失，**公司设置页面将无法保存 ITS 密码**（前端和后端都会拦截）。

### 2.3 如何禁用 ITS（3 种方式）

| 方式 | 操作 | 效果 |
|------|------|------|
| **① 清空配置（推荐）** | 公司设置 → ITS 系统地址清空 → 保存 | 所有 `/api/its/*` 接口立即返回"请先配置"，零代码改动 |
| **② 删除路由** | 注释掉 app.py 中 3 个 `@app.route('/api/its/...)` 块（L5960-L6106） | 接口直接 404，前端工单面板不再可用 |
| **③ 代码级开关** | 在 `.env` 加 `ITS_ENABLED=false`，在路由开头加 `if not ITS_ENABLED: return jsonify({'success':False, 'message':'ITS 已禁用'})` | 可运行时开关，不破坏路由结构 |

---

## 三、JeecgBoot ITS 协议细节

当前 `ITSService` 实现**硬编码了以下协议细节**，对接其他系统时需要逐项对比修改。

### 3.1 API 端点（均在 `its_service.py` 中）

| 功能 | 方法 | 当前端点 | 代码行 |
|------|------|----------|--------|
| 获取验证码 | GET | `{base_url}/its-api/sys/randomImage/{checkKey}` | L160 |
| 登录 | POST | `{base_url}/its-api/sys/login` | L274 |
| 登出 | POST | `{base_url}/its-api/sys/logout` | L444 |
| 工单列表 | GET | `{base_url}/its-api/biz/itsBizJob/list` | L375 |

### 3.2 请求/响应格式

**登录请求体**（L275-L280）：
```json
{
  "username": "admin",
  "password": "xxx",
  "captcha": "aB3x",
  "checkKey": "1732681234567"
}
```

**登录成功响应**（L287-L296）：
```json
{
  "success": true,
  "result": {
    "token": "eyJhbGciOiJIUzI1NiJ9...",
    "username": "admin",
    ...
  }
}
```
> 兼容 JeecgBoot 多种响应格式：`success=true` / `code=200` / `code=0`

**鉴权头**（L385）：
```
X-Access-Token: eyJhbGciOiJIUzI1NiJ9...
```

**工单查询参数**（L376-L384）：
```
_t          = 毫秒级时间戳（防缓存）
storecode   = 门店编号（数字字符串，如 "316967"）
column      = createTime   （排序字段）
order       = desc         （降序）
field       = id           （另一个过滤字段）
pageNo      = 1
pageSize    = 2
```

**工单响应字段 → 内部映射**（L417-L432）：

| ITS 原始字段 | 内部字段 | 含义 | 兜底备选 |
|-------------|----------|------|----------|
| `jobcode` / `jobCode` | `work_order_number` | 工单编号 | - |
| `storecode_dictText` | `store_name` | 门店名称（含店号） | `storename` / `storeName` |
| `createTime` | `create_time` | 创建时间 | - |
| `depict` | `description` | 问题描述 | `description` / `content` |

> ⚠️ JeecgBoot 对列表字段常用 `xxx_dictText` 格式（字典翻译后的值），
> 对接其他系统时**大概率不存在这个字段**，必须改 `_map_work_order()`。

---

## 四、对接新工单系统的修改指南

以下以"通用步骤 + 具体代码定位"的形式呈现。每一条标注了需要修改的**文件 / 类 / 方法 / 行号范围**。

### 步骤 1：确定新系统的 API 协议

先用 Postman / curl 搞清楚新系统的：
- 登录端点、请求体格式、返回的 token 字段名
- Token 放在哪个 Header 里
- 工单查询端点、参数名（尤其是门店字段名！）
- 工单返回的字段名

### 步骤 2：修改 `its_service.py` — ITSService 类

#### 2.1 登录端点和鉴权头

**`fetch_captcha()` 方法 (L149-L175)**：
```python
# 当前：
url = f'{self.base_url}/its-api/sys/randomImage/{check_key}'
# 改成新系统的验证码端点，比如：
url = f'{self.base_url}/api/auth/captcha?key={check_key}'
```
返回值格式变了（比如不是 base64 而是 URL）也在这里处理。

**`login()` 方法 (L264-L300)**：
```python
# 当前请求体 (L275-L280):
payload = {
    'username': self.account,
    'password': self.password,
    'captcha': captcha,
    'checkKey': check_key
}
# 改成新系统需要的格式，比如有些系统用 grant_type:
payload = {'grant_type': 'password', 'username': self.account, 'password': self.password}

# 当前 token 提取 (L288):
token = data.get('result', {}).get('token', '')
# 新系统可能是 data['access_token'] 或其他路径
```

**请求头 (L106-L110, L385)**：
```python
# 当前:
headers = {'X-Access-Token': token}
# 新系统可能:
headers = {'Authorization': f'Bearer {token}'}
# 改成能全局生效的方式——在 ITSService.__init__ 添加 _auth_header 或重写一个 _get_auth_headers(token) 方法
```

#### 2.2 工单查询端点和参数

**`query_work_orders()` 方法 (L359-L414)**：
```python
# 当前端点 (L375):
url = f'{self.base_url}/its-api/biz/itsBizJob/list'
# 改成新系统端点，比如:
url = f'{self.base_url}/api/tickets'

# 当前参数 (L376-L384):
params = {
    '_t': self._get_timestamp(),
    'storecode': store_code,     # ← 新系统字段名大概率不同！
    'column': 'createTime',
    'order': 'desc',
    'field': 'id',
    'pageNo': 1,
    'pageSize': page_size
}
# 改成:
params = {
    'shop_code': store_code,     # 换成新系统的门店字段
    'sort': '-created_at',
    'page': 1,
    'size': page_size
}
```

#### 2.3 工单字段映射

**`_map_work_order()` 静态方法 (L417-L432)** — **这是最常改的地方**：
```python
# 当前实现（JeecgBoot）:
@staticmethod
def _map_work_order(record):
    return {
        'work_order_number': record.get('jobcode') or record.get('jobCode') or '',
        'store_name': record.get('storecode_dictText') or record.get('storename') or record.get('storeName') or '',
        'create_time': record.get('createTime') or '',
        'description': record.get('depict') or record.get('description') or record.get('content') or '',
    }

# 改成新系统字段，比如 ServiceNow:
@staticmethod
def _map_work_order(record):
    return {
        'work_order_number': record.get('number') or '',
        'store_name': record.get('location', {}).get('display_value', '') or record.get('store_name', ''),
        'create_time': record.get('opened_at') or record.get('sys_created_on') or '',
        'description': record.get('short_description') or record.get('description') or '',
    }
```

> **映射层的好处**：前端只认 `work_order_number` / `store_name` / `create_time` / `description` 这 4 个内部字段，
> `_map_work_order()` 就是隔离层，改它就能兼容任何后端。

#### 2.4 Token TTL 和端点常量（文件顶部）

```python
TOKEN_TTL = 1500              # L38 — 当前 25 分钟 (JeecgBoot 默认 30 分钟，留 5 分钟余量)
                             # 新系统如果 token 1 小时有效 → 设 3300
                             # 如果新系统不返回有效期 → 需根据 expires_in 动态计算

# 建议把硬编码路径提到类常量，方便整体修改：
class ITSService:
    CAPTCHA_ENDPOINT = '/its-api/sys/randomImage'
    LOGIN_ENDPOINT   = '/its-api/sys/login'
    LOGOUT_ENDPOINT  = '/its-api/sys/logout'
    WORKORDERS_ENDPOINT = '/its-api/biz/itsBizJob/list'
```

#### 2.5 如果新系统**没有验证码**

JeecgBoot 以外的多数系统（JWT 直签 / Basic Auth）不需要验证码。改 `get_valid_token()` (L302-L353)：

```python
def get_valid_token(self, max_retries=3):
    # 1. 检查缓存（不变）

    # 2. 直接登录，跳过验证码流程
    result = self._simple_login()  # 新增一个无验证码的登录方法
    if 'token' in result:
        return {'token': result['token']}
    return {'error': result.get('error', '登录失败')}
```

同时路由层 `app.py` 的 `/api/its/captcha` 和 `/api/its/login` 也可以一起简化或删除。

---

## 五、验证码 OCR 调优（如果新系统仍需验证码）

### 5.1 当前 OCR 策略（L177-L258）

JeecgBoot ITS 验证码特征：**4 位混合大小写字母+数字，橙金色渐变字符 + 灰色噪点背景**。

预处理流水线（L204-L235）：
```
原图 → BGR→HSV → 颜色过滤（橙金色 H:10-50, S:40-255, V:80-255）
     → 灰度 / mask → 放大 4x → 中值滤波去噪 → Otsu 二值化 → 反转为白底黑字
     → Tesseract psm 7/8/13 三种配置择优取 4-5 位结果
```

### 5.2 新系统验证码不同怎么办

**改 `ocr_captcha()` 方法**，常见情况：

| 新系统验证码特征 | 修改方向 |
|------------------|----------|
| 纯黑底白字，无噪点 | 去掉 HSV 过滤，直接灰度 → 二值化 |
| 扭曲 / 带干扰线 | 增加形态学开运算去细线：`kernel=np.ones((2,2),np.uint8); cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)` |
| 中文 | Tesseract 语言包加 `chi_sim`，whitelist 去掉 |
| 6 位数字 | `tessedit_char_whitelist` 只保留 `0-9`，psm 4 更合适 |
| 极难识别 | **换思路**：让 OCR 失败后前端弹框手动输入，已支持（`need_manual` 标志） |

### 5.3 验证码端点返回格式不同

当前 `fetch_captcha()` (L166-L173) 处理两种：
```python
# 情况 1: "data:image/jpg;base64,xxxx" → 带 data URI 前缀
# 情况 2: "xxxx"                      → 纯 base64
if result.startswith('data:image'):
    base64_data = result.split(',', 1)[1]
else:
    base64_data = result
```

新系统如果返回 URL 而非 base64，改成：
```python
if result.startswith('http'):
    resp2 = self._session.get(result, timeout=10)
    return base64.b64encode(resp2.content).decode(), check_key
```

---

## 六、配置面板（前端）

公司设置页面已有 ITS 三字段（URL / 账号 / 密码）：
- 路径：`设置` → 公司编辑
- 前端组件：`templates/settings.html` 中的公司编辑 modal
- 后端：`POST /api/companies` 和 `PUT /api/companies/<id>`（L5870-L5941）

如果新系统需要额外配置项（比如 OAuth client_id/secret），需要同时修改三处：

| 位置 | 修改内容 |
|------|----------|
| `companies` 表 | `ALTER TABLE companies ADD COLUMN its_extra_config TEXT;` |
| `app.py` L793-L795 附近 | 在 `ensure_column_exists` 块里追加新列定义 |
| `app.py` L969 / L1000 | `add_company()` / `update_company()` 方法加参数 |
| `templates/settings.html` | 公司编辑 modal 加输入框 |

---

## 七、快速验证清单

改完 ITS 集成后按顺序验证：

```bash
# 1. 端点通不通
curl -v https://新ITS地址/其登录端点

# 2. 验证码 OCR 是否工作
python -c "
from its_service import ITSService, check_tesseract_available
ok, msg = check_tesseract_available()
print(f'Tesseract: {ok} {msg}')
"

# 3. 登录 + token 获取
python -c "
from its_service import ITSService
svc = ITSService('https://新地址', '账号', '密码', 1)
print(svc.get_valid_token())
svc.close()
"

# 4. 工单查询 + 字段映射
python -c "
from its_service import ITSService
svc = ITSService('https://新地址', '账号', '密码', 1)
r = svc.query_work_orders('门店编号', page_size=2)
import json
print(json.dumps(r, ensure_ascii=False, indent=2))
svc.close()
"

# 5. 接口层验证
curl -H "Authorization: Bearer <app-jwt>" http://localhost:5050/api/its/workorders/316967
```

---

## 八、文件定位速查

| 文件 | 角色 | 修改频率 |
|------|------|----------|
| [its_service.py](file:///c:/web/effic/its_service.py) | **核心协议层**（API 端点、字段映射、OCR 预处理） | 对接新 ITS 时必须改 |
| [app.py](file:///c:/web/effic/app.py) @ L5960-L6106 | 路由层（3 个 `/api/its/*` 端点） | 极少改，除非彻底禁用或换接口 |
| [app.py](file:///c:/web/effic/app.py) @ L793-L795 | 数据库列扩展 | 加新 ITS 字段时改 |
| [crypto_util.py](file:///c:/web/effic/crypto_util.py) | 凭据加解密 | 不改 |
| [templates/settings.html](file:///c:/web/effic/templates/settings.html) | 前端公司设置面板 | 加新字段时改 |
| `.env` | `ITS_SECRET_KEY` | 部署时配置一次 |

---

## 九、常见场景的一句话方案

| 想做什么 | 改哪里 | 工作量 |
|----------|--------|--------|
| **禁用 ITS** | 公司设置清空 `its_url` | 0 代码 |
| **换 ITS 系统（仍 JeecgBoot）** | 只改公司设置的 URL/账号/密码 | 0 代码 |
| **换 ITS 系统（接口不同）** | `its_service.py` 里 4 个端点路径 + `_map_work_order()` | 约 30 行 |
| **新 ITS 无验证码** | `its_service.py#get_valid_token()` 重写 + 删路由层 captcha/login | 约 50 行 |
| **验证码识别不准** | `its_service.py#ocr_captcha()` HSV 范围 / tesseract psm 配置 | 约 10 行 |
| **Token TTL 不同** | `its_service.py` 文件顶部 `TOKEN_TTL` 常量 | 1 行 |
| **换 Bearer / Basic Auth** | `ITSService.__init__` 加 `_get_auth_headers()` 方法 | 约 10 行 |
| **工单字段名完全不同** | `_map_work_order()` 静态方法 | 约 10 行 |
| **加 ITS 额外配置项** | 数据库 ALTER + `add_company`/`update_company` + settings.html | 约 20 行 |

---

*相关文档：[FEATURES.md](FEATURES.md) · [DEPLOYMENT.md](DEPLOYMENT.md)*
