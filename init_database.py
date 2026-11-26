"""
数据库初始化脚本
用于一键创建数据库和用户
支持交互模式和非交互模式（通过命令行参数）
用法:
  交互模式: python init_database.py
  非交互模式: python init_database.py --password=your_root_password
"""
import os
import sys
import pymysql

def init_database(root_password=None):
    print("=" * 50)
    print("设备库存管理系统 - 数据库初始化")
    print("=" * 50)

    if root_password is None:
        root_password = input("请输入MySQL root密码: ")
        confirm = input("\n确认创建? (y/n): ")
        if confirm.lower() != 'y':
            print("已取消")
            return

    db_name = os.environ.get('DB_DATABASE', 'device_inventory_system')
    db_user = os.environ.get('DB_USER', '') or 'effic'
    db_password = os.environ.get('DB_PASSWORD', '')

    # 交互模式下若未从环境变量获取到密码，则强制提示输入（不再使用硬编码默认值）
    if not db_password:
        try:
            db_password = input(f"请输入数据库用户 '{db_user}' 的密码 (建议使用强密码): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return

    print("\n配置信息:")
    print(f"  数据库名: {db_name}")
    print(f"  用户名: {db_user}")
    print(f"  密码: {'*' * len(db_password) if db_password else '(空)'}")
    print(f"  Host: localhost")

    try:
        conn = pymysql.connect(
            host='localhost',
            user='root',
            password=root_password,
            charset='utf8mb4',
            connect_timeout=10
        )
        cursor = conn.cursor()

        print("\n正在创建数据库...")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        print("数据库创建成功")

        print("\n正在创建用户...")
        try:
            cursor.execute(f"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_password}'")
        except pymysql.err.OperationalError as e:
            code, msg = e.args
            if code == 1396:
                print(f"用户 '{db_user}' 已存在，跳过创建")
            else:
                raise e

        print("\n正在授权...")
        cursor.execute(f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost'")
        cursor.execute("FLUSH PRIVILEGES")
        print("授权成功")

        cursor.close()
        conn.close()

        print("\n" + "=" * 50)
        print("数据库初始化完成!")
        print("=" * 50)
        print("\n下一步:")
        print("1. 启动应用: python app.py")
        print("2. 访问: http://localhost:5050")
        print("3. 登录账号: admin / admin123")

    except pymysql.err.OperationalError as e:
        code, msg = e.args
        print(f"\n错误 (代码 {code}): {msg}")
        if code == 1045:
            print("原因: root密码错误，请确认后重试")
            print("提示: 如果忘记了密码，可以尝试以管理员身份运行:")
            print("   mysqld --skip-grant-tables")
        elif code == 2003:
            print("原因: 无法连接到MySQL，请确保MySQL服务正在运行")
            print("提示: 按 Win+R，输入 services.msc，找到 MySQL 服务并启动")
    except Exception as e:
        print(f"\n错误: {e}")
        print("请确保MySQL服务正在运行")

    print("")

if __name__ == '__main__':
    # 检查命令行参数
    root_password = None
    for arg in sys.argv[1:]:
        if arg.startswith('--password='):
            root_password = arg.split('=', 1)[1]
        elif arg == '--help':
            print("用法: python init_database.py [--password=root密码]")
            print("  --password: MySQL root密码（可选，不传则交互输入）")
            sys.exit(0)

    init_database(root_password)
