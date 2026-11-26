"""its_password 明文 -> 密文 一次性迁移脚本（幂等，可重复执行）

使用前置条件：
    1) 已部署 crypto_util.py，并已在环境变量中配置 ITS_SECRET_KEY
       （IIS 下为 web.config 的 appSettings；本地为 .env）；
    2) 建议先用备份（脚本会自动建备份表）。

执行：
    cd C:\\luosen\\effic
    C:\\Python312\\python.exe migrate_its_password.py

行为：
    - 自动创建备份表 companies_bak_<日期>（已存在则跳过）；
    - 将 its_password 列长度扩到 VARCHAR(512)（Fernet 密文比明文长约 57 字符）；
    - 仅对「非空且不带 enc: 前缀」的行加密，因此可反复执行而不会重复加密；
    - 逐行打印结果，便于核对。
"""

import os
import sys
from datetime import datetime

# 保证可 import 同目录下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pymysql
from dotenv import load_dotenv

if not load_dotenv('.env', override=False):
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=False)

import crypto_util


def main():
    if not crypto_util.is_enabled():
        print('[ABORT] 未配置 ITS_SECRET_KEY，请先配置密钥再执行迁移')
        return 1

    host = os.environ.get('DB_HOST', 'localhost')
    user = os.environ.get('DB_USER')
    password = os.environ.get('DB_PASSWORD')
    database = os.environ.get('DB_DATABASE')
    print(f'[INFO] 目标数据库: {host}/{database}')

    conn = pymysql.connect(host=host, user=user, password=password,
                           database=database, charset='utf8mb4', autocommit=False)
    cur = conn.cursor(pymysql.cursors.DictCursor)
    try:
        # 1) 备份
        bak = 'companies_bak_' + datetime.now().strftime('%Y%m%d')
        cur.execute("SHOW TABLES LIKE %s", (bak,))
        if cur.fetchone():
            print(f'[SKIP] 备份表 {bak} 已存在，跳过备份')
        else:
            cur.execute(f"CREATE TABLE {bak} AS SELECT * FROM companies")
            print(f'[OK] 已创建备份表 {bak}')

        # 2) 扩列（Fernet 密文更长）
        cur.execute("ALTER TABLE companies MODIFY its_password VARCHAR(512)")
        print('[OK] its_password 列长度已调整为 VARCHAR(512)')

        # 3) 逐行加密（仅处理无 enc: 前缀的行）
        cur.execute("SELECT id, company_code, its_password FROM companies")
        rows = cur.fetchall()
        changed = 0
        for row in rows:
            raw = row.get('its_password') or ''
            if not raw:
                print(f'[SKIP] id={row["id"]} {row["company_code"]}: 密码为空')
                continue
            if raw.startswith(crypto_util.ENC_PREFIX):
                print(f'[SKIP] id={row["id"]} {row["company_code"]}: 已是密文')
                continue
            cur.execute("UPDATE companies SET its_password = %s WHERE id = %s",
                        (crypto_util.encrypt_secret(raw), row['id']))
            changed += 1
            print(f'[OK]   id={row["id"]} {row["company_code"]}: 已加密')

        conn.commit()
        print(f'[DONE] 迁移完成，共加密 {changed} 条记录')

        # 4) 校验
        cur.execute("SELECT id, company_code, LEFT(its_password,4) AS prefix FROM companies")
        for r in cur.fetchall():
            flag = 'OK ' if (r['prefix'] or '').startswith('enc') or not r['prefix'] else 'CHECK'
            print(f'  [{flag}] id={r["id"]} {r["company_code"]} prefix={r["prefix"]!r}')
    except Exception as e:
        conn.rollback()
        print(f'[FAIL] 迁移失败，已回滚: {e}')
        return 1
    finally:
        cur.close()
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
