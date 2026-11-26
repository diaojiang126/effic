"""敏感字段加密工具（用于 companies.its_password 等）

设计要点：
1. 对称加密（Fernet = AES-128-CBC + HMAC-SHA256），因 ITS 登录需要明文，不能使用哈希；
2. 密文统一带前缀 ``enc:``，与历史明文共存，便于平滑迁移与回滚；
3. 密钥只从环境变量 ``ITS_SECRET_KEY`` 读取，绝不写入代码或数据库；
   支持逗号分隔多把密钥：**第一把用于加密，全部用于解密**（便于密钥轮换）；
4. 未配置密钥时：解密（兼容明文）可正常工作，加密会抛错，避免敏感字段被静默明文落库。

环境变量配置（IIS 下写在 web.config 的 appSettings 中优先生效）：
    ITS_SECRET_KEY=<Fernet.generate_key() 生成的 44 位 base64 字符串>
生成方式：
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import os

ENC_PREFIX = 'enc:'          # 密文前缀：用于区分密文与历史明文
PLACEHOLDER = '******'       # 前端回显用的脱敏占位符（提交该值表示“不修改密码”）

_fernets = []
try:
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
    for _raw in (os.environ.get('ITS_SECRET_KEY') or '').split(','):
        _key = _raw.strip()
        if not _key:
            continue
        try:
            _fernets.append(Fernet(_key.encode()))
        except Exception as e:
            print(f"[WARN] ITS_SECRET_KEY 无效，已跳过: {e}")
except ImportError as e:
    print(f"[WARN] 未安装 cryptography，敏感字段加密不可用: {e}")


def is_enabled():
    """是否已配置可用的加密密钥"""
    return len(_fernets) > 0


def encrypt_secret(plain):
    """明文 -> 'enc:<token>'；空值原样返回；未配置密钥时抛错（拒绝明文落库）"""
    if not plain:
        return plain or ''
    if not is_enabled():
        raise RuntimeError('未配置 ITS_SECRET_KEY，拒绝以明文写入敏感字段')
    return ENC_PREFIX + _fernets[0].encrypt(plain.encode('utf-8')).decode('ascii')


def decrypt_secret(stored):
    """'enc:<token>' -> 明文；无前缀（历史明文）原样返回；解密失败抛错"""
    if not stored:
        return ''
    if not stored.startswith(ENC_PREFIX):
        return stored                       # 兼容历史明文数据
    if not is_enabled():
        raise RuntimeError('未配置 ITS_SECRET_KEY，无法解密敏感字段')
    token = stored[len(ENC_PREFIX):].encode('ascii')
    for fernet in _fernets:
        try:
            return fernet.decrypt(token).decode('utf-8')
        except Exception:
            continue
    raise RuntimeError('敏感字段解密失败：密钥不匹配（请检查 ITS_SECRET_KEY）')
