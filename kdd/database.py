import sqlite3
import hashlib
import os
import re
from datetime import datetime
from config import DATABASE_PATH

DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

def get_db_connection():
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=30000')
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError as e:
        if 'readonly' in str(e).lower():
            conn = sqlite3.connect(f'file:{DATABASE_PATH}?mode=rw', uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        raise

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=30000')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

        cursor.execute('SELECT COUNT(*) FROM users')
        if cursor.fetchone()[0] == 0:
            cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                          ('admin', hash_password('admin123'), 'admin'))
            conn.commit()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS express_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_name TEXT,
                sender_phone TEXT,
                sender_address TEXT,
                receiver_name TEXT,
                receiver_phone TEXT,
                receiver_address TEXT,
                amount TEXT,
                express_date TEXT,
                express_no TEXT,
                full_text TEXT,
                image_path TEXT,
                project TEXT,
                record_type TEXT,
                created_by TEXT,
                status TEXT DEFAULT 'saved',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS custom_options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                option_type TEXT,
                option_value TEXT,
                UNIQUE(option_type, option_value)
            )
        ''')
        conn.commit()
        try:
            cursor.execute('ALTER TABLE express_records ADD COLUMN express_date TEXT')
            conn.commit()
        except:
            pass
        try:
            cursor.execute('ALTER TABLE express_records ADD COLUMN express_no TEXT')
            conn.commit()
        except:
            pass
        try:
            cursor.execute('ALTER TABLE express_records ADD COLUMN project TEXT')
            conn.commit()
        except:
            pass
        try:
            cursor.execute('ALTER TABLE express_records ADD COLUMN record_type TEXT')
            conn.commit()
        except:
            pass
        try:
            cursor.execute('ALTER TABLE express_records ADD COLUMN created_by TEXT')
            conn.commit()
        except:
            pass
        try:
            cursor.execute('ALTER TABLE express_records ADD COLUMN item_name TEXT')
            conn.commit()
        except:
            pass
        try:
            cursor.execute('ALTER TABLE express_records ADD COLUMN client_token TEXT')
            conn.commit()
        except:
            pass
        # 记录状态：draft=上传识别后待用户核对保存的草稿；saved=已保存。
        # 存量数据与旧代码写入的记录一律视为 saved。
        try:
            cursor.execute("ALTER TABLE express_records ADD COLUMN status TEXT DEFAULT 'saved'")
            conn.commit()
        except:
            pass
        # 幂等键唯一索引：弱网重试导致的重复提交只保留一条
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_express_records_client_token
            ON express_records(client_token)
            WHERE client_token IS NOT NULL AND client_token != ''
        ''')
        # 录入时间索引：列表排序使用
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_express_records_created_at
            ON express_records(created_at)
        ''')
        # 寄件日期索引：按单据日期范围导出时避免全表扫描
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_express_records_express_date
            ON express_records(express_date)
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database initialization error: {e}")
        if os.path.exists(DATABASE_PATH):
            backup_path = DATABASE_PATH + '.backup'
            import shutil
            shutil.copy(DATABASE_PATH, backup_path)
            print(f"Database backed up to: {backup_path}")
        raise

def verify_user(username, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ? AND password = ?',
                  (username, hash_password(password)))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def get_user_by_username(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    conn.close()
    return dict(user) if user else None

def create_user(username, password, role='user'):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                      (username, hash_password(password), role))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, role, created_at FROM users ORDER BY id')
    users = cursor.fetchall()
    conn.close()
    return [dict(row) for row in users]

def delete_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

def update_user_password(user_id, new_password):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET password = ? WHERE id = ?',
                  (hash_password(new_password), user_id))
    conn.commit()
    conn.close()

# 允许通过更新接口写入的字段白名单（status 由保存/草稿流程单独控制，不对外暴露）
UPDATABLE_FIELDS = ('sender_name', 'sender_phone', 'sender_address', 'receiver_name',
                    'receiver_phone', 'receiver_address', 'amount', 'express_date',
                    'express_no', 'full_text', 'project', 'record_type', 'item_name')

# 列表/导出/预选一律只针对「已保存」记录：草稿是上传后待用户核对的中间态，对用户不可见
SAVED_RECORDS_FILTER = "COALESCE(status, 'saved') = 'saved'"


def insert_record(data, created_by='', client_token='', status='saved'):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO express_records
            (sender_name, sender_phone, sender_address,
             receiver_name, receiver_phone, receiver_address,
             amount, express_date, express_no, full_text, image_path,
             project, record_type, item_name, created_by, created_at, client_token, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('sender_name', ''),
            data.get('sender_phone', ''),
            data.get('sender_address', ''),
            data.get('receiver_name', ''),
            data.get('receiver_phone', ''),
            data.get('receiver_address', ''),
            data.get('amount', ''),
            data.get('express_date', ''),
            data.get('express_no', ''),
            data.get('full_text', ''),
            data.get('image_path', ''),
            data.get('project', ''),
            data.get('record_type', ''),
            data.get('item_name', ''),
            created_by,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            client_token or None,
            status or 'saved'
        ))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_record_by_client_token(client_token):
    if not client_token:
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM express_records WHERE client_token = ?', (client_token,))
    record = cursor.fetchone()
    conn.close()
    return dict(record) if record else None


def insert_record_once(data, created_by='', client_token='', status='saved'):
    """幂等写入：同一 client_token 重复提交只保留一条，返回 (record_id, 是否新建)。"""
    if client_token:
        existing = get_record_by_client_token(client_token)
        if existing:
            return existing['id'], False
    try:
        return insert_record(data, created_by, client_token, status), True
    except sqlite3.IntegrityError:
        existing = get_record_by_client_token(client_token)
        if existing:
            return existing['id'], False
        raise

def update_record(record_id, data):
    """局部更新：只写入调用方显式提交的字段。

    未提交的字段保持原值，避免像 full_text（识别全文）这类大字段被空值意外覆盖。
    """
    fields = [field for field in UPDATABLE_FIELDS if field in data]
    if not fields:
        return
    assignments = ', '.join(f'{field} = ?' for field in fields)
    params = [data[field] for field in fields]
    params.append(record_id)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f'UPDATE express_records SET {assignments} WHERE id = ?', params)
    conn.commit()
    conn.close()


def finalize_records(records):
    """把一批草稿记录补全字段并标记为已保存。

    要求每条记录都已填写项目与类型，且全部通过校验才整体写入（事务内保证原子性）；
    否则抛 ValueError，由调用方转成用户可读提示。返回保存条数。
    """
    if not records:
        raise ValueError('没有可保存的记录')

    normalized = []
    for item in records:
        try:
            record_id = int(item.get('id'))
        except (TypeError, ValueError):
            raise ValueError('存在无效的记录，请重新上传')
        project = str(item.get('project') or '').strip()
        record_type = str(item.get('record_type') or '').strip()
        if not project:
            raise ValueError('请为所有文件选择项目')
        if not record_type:
            raise ValueError('请为所有文件选择类型')
        normalized.append((record_id, item, project, record_type))

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for record_id, item, project, record_type in normalized:
            cursor.execute('SELECT * FROM express_records WHERE id = ?', (record_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError('部分记录不存在，请重新上传')
            merged = dict(row)
            for field in UPDATABLE_FIELDS:
                if field in item:
                    merged[field] = item[field]
            merged['project'] = project
            merged['record_type'] = record_type
            cursor.execute('''
                UPDATE express_records SET
                    sender_name = ?, sender_phone = ?, sender_address = ?,
                    receiver_name = ?, receiver_phone = ?, receiver_address = ?,
                    amount = ?, express_date = ?, express_no = ?, full_text = ?,
                    project = ?, record_type = ?, item_name = ?, status = 'saved'
                WHERE id = ?
            ''', (
                merged.get('sender_name', ''), merged.get('sender_phone', ''),
                merged.get('sender_address', ''), merged.get('receiver_name', ''),
                merged.get('receiver_phone', ''), merged.get('receiver_address', ''),
                merged.get('amount', ''), merged.get('express_date', ''),
                merged.get('express_no', ''), merged.get('full_text', ''),
                project, record_type, merged.get('item_name', ''),
                record_id
            ))
        conn.commit()
        return len(normalized)
    finally:
        conn.close()

def get_all_records():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f'SELECT * FROM express_records WHERE {SAVED_RECORDS_FILTER} ORDER BY created_at DESC')
    records = cursor.fetchall()
    conn.close()
    return [dict(row) for row in records]

# 列表接口下发字段：不含 full_text（识别全文体积大，仅在详情接口下发）
LIST_COLUMNS = ('id', 'sender_name', 'sender_phone', 'sender_address', 'receiver_name',
                'receiver_phone', 'receiver_address', 'item_name', 'amount', 'express_date',
                'express_no', 'image_path', 'project', 'record_type', 'created_by', 'created_at')


def get_records_page(limit=None, offset=0, slim=False):
    """列表分页查询，返回 (records, total)。

    slim=True 时不下发 full_text，用于弱网场景显著减小响应体积；
    limit 为 None 时返回全部（保持旧行为）。
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f'SELECT COUNT(*) FROM express_records WHERE {SAVED_RECORDS_FILTER}')
    total = cursor.fetchone()[0]

    columns = ', '.join(LIST_COLUMNS) if slim else '*'
    sql = (f'SELECT {columns} FROM express_records WHERE {SAVED_RECORDS_FILTER} '
           f'ORDER BY created_at DESC, id DESC')
    params = []
    if limit:
        sql += ' LIMIT ? OFFSET ?'
        params = [int(limit), max(0, int(offset or 0))]

    cursor.execute(sql, params)
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return records, total


def normalize_date_range(start_date, end_date):
    """校验并规范化导出用的日期范围，返回 (start, end)。

    格式非法或「开始晚于结束」时抛 ValueError，由调用方转成用户可读提示。
    """
    start = (start_date or '').strip()
    end = (end_date or '').strip()
    for label, value in (('开始日期', start), ('结束日期', end)):
        if not value:
            continue
        if not DATE_PATTERN.match(value):
            raise ValueError(f'{label}格式不正确，应为 YYYY-MM-DD')
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            raise ValueError(f'{label}不是有效日期: {value}')
    if start and end and start > end:
        raise ValueError('开始日期不能晚于结束日期')
    return start, end


def get_records_by_express_date(start_date=None, end_date=None):
    """按快递单上的**寄件日期**（express_date）查询记录，含首尾两天；两者为空表示不限。

    express_date 统一存为 YYYY-MM-DD 文本，字符串比较等价于日期比较；
    条件落在单列上，可命中 idx_express_records_express_date 索引，避免全表扫描。

    注意：未识别到寄件日期的记录（express_date 为空）不会被任何日期范围命中。
    """
    sql = 'SELECT * FROM express_records'
    # 草稿（上传后待核对的中间态）不参与导出
    conditions = [SAVED_RECORDS_FILTER]
    params = []
    if start_date:
        conditions.append('express_date >= ?')
        params.append(start_date)
    if end_date:
        conditions.append('express_date <= ?')
        params.append(end_date)
    if start_date or end_date:
        # 未识别到寄件日期的记录（express_date 为空串）在字符串比较下会满足 <= 条件，
        # 必须显式排除，否则「只填结束日期」时会把它们混进结果。
        # 不限范围时不加此条件，保持「导出全部」的原有行为。
        conditions.append("express_date IS NOT NULL AND express_date != ''")
    sql += ' WHERE ' + ' AND '.join(conditions)
    # 排序沿用录入时间倒序，与不带范围时的原有行为保持一致
    sql += ' ORDER BY created_at DESC, id DESC'

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return records


# 智能预选的最小置信度：至少 N 条历史记录，且最高频组合占比不低于 RATIO
SUGGESTION_MIN_OCCURRENCES = 3
SUGGESTION_MIN_RATIO = 0.6


def suggest_project_type(sender_name='', sender_address='', receiver_name='', receiver_address=''):
    """依据历史记录中「同一寄件人/地址」的高频组合，为项目与类型做预选。

    只有同时达到最小出现次数与占比才返回，避免把偶发组合当成规律。
    返回 {'project': ..., 'record_type': ...}，无可靠结论时返回 {}。
    """
    conditions = []
    params = []
    for column, value in (('sender_name', sender_name), ('sender_address', sender_address),
                          ('receiver_name', receiver_name), ('receiver_address', receiver_address)):
        if value:
            conditions.append(f'{column} = ?')
            params.append(value)
    if not conditions:
        return {}

    where = (f"({' OR '.join(conditions)}) "
             f"AND {SAVED_RECORDS_FILTER} "
             f"AND (COALESCE(project, '') != '' OR COALESCE(record_type, '') != '')")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f'SELECT COUNT(*) FROM express_records WHERE {where}', params)
    matched_total = cursor.fetchone()[0]
    if matched_total < SUGGESTION_MIN_OCCURRENCES:
        conn.close()
        return {}

    cursor.execute(f'''
        SELECT project, record_type, COUNT(*) AS cnt
        FROM express_records
        WHERE {where}
        GROUP BY project, record_type
        ORDER BY cnt DESC
        LIMIT 5
    ''', params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not rows:
        return {}
    top = rows[0]
    if top['cnt'] < SUGGESTION_MIN_OCCURRENCES or top['cnt'] < matched_total * SUGGESTION_MIN_RATIO:
        return {}
    return {
        'project': top['project'] or '',
        'record_type': top['record_type'] or '',
    }


def apply_option_suggestion(result):
    """为识别结果中空白的项目/类型做高置信度预选（用户仍可在界面上修改）。"""
    if result.get('project') and result.get('record_type'):
        return result
    suggestion = suggest_project_type(
        sender_name=result.get('sender_name', ''),
        sender_address=result.get('sender_address', ''),
        receiver_name=result.get('receiver_name', ''),
        receiver_address=result.get('receiver_address', ''),
    )
    if not suggestion:
        return result
    if not result.get('project') and suggestion.get('project'):
        result['project'] = suggestion['project']
    if not result.get('record_type') and suggestion.get('record_type'):
        result['record_type'] = suggestion['record_type']
    return result


def get_record_by_id(record_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM express_records WHERE id = ?', (record_id,))
    record = cursor.fetchone()
    conn.close()
    return dict(record) if record else None

def delete_record(record_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM express_records WHERE id = ?', (record_id,))
    conn.commit()
    conn.close()

def get_custom_options(option_type):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT option_value FROM custom_options WHERE option_type = ? ORDER BY id', (option_type,))
    records = cursor.fetchall()
    conn.close()
    return [row[0] for row in records]

def add_custom_option(option_type, option_value):
    if not option_value.strip():
        return False
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT OR IGNORE INTO custom_options (option_type, option_value) VALUES (?, ?)', (option_type, option_value.strip()))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False

def delete_custom_option(option_type, option_value):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM custom_options WHERE option_type = ? AND option_value = ?', (option_type, option_value))
    conn.commit()
    conn.close()

def get_record_ids_by_month(year, month):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, image_path FROM express_records
        WHERE strftime('%Y', created_at) = ? AND strftime('%m', created_at) = ?
    ''', (str(year), str(month).zfill(2)))
    records = cursor.fetchall()
    conn.close()
    return [(row['id'], row['image_path']) for row in records]

def delete_records_by_month(year, month):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM express_records
        WHERE strftime('%Y', created_at) = ? AND strftime('%m', created_at) = ?
    ''', (str(year), str(month).zfill(2)))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count

def get_all_image_paths():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT image_path FROM express_records WHERE image_path IS NOT NULL AND image_path != ""')
    paths = [row['image_path'] for row in cursor.fetchall()]
    conn.close()
    return paths

init_db()