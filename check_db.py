import pymysql
from datetime import datetime

print(f"[{datetime.now()}] 重新测试数据库连接...")

config = {
    'host': 'localhost',
    'port': 3306,
    'user': 'ls',
    'password': 'ls1213456',
    'database': 'device_inventory_system',
    'charset': 'utf8mb4'
}

try:
    conn = pymysql.connect(**config)
    print(" 应用用户连接成功")
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES")
    tables = cursor.fetchall()
    print(f"\n共有 {len(tables)} 个表:")
    for tbl in tables:
        print(f"  - {tbl[0]}")
    
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    print(f"\nusers 表有 {count} 条记录")
    
    cursor.close()
    conn.close()
    print("\n 数据库连接正常！")
    
except Exception as e:
    print(f"\n 应用用户连接失败: {e}")
    print("\n尝试初始化数据库...")
    import sys
    sys.path.insert(0, r'c:\luosen\effic')
    from init_database import init_database
    init_database()
