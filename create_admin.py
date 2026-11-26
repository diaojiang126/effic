#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建初始管理员用户脚本
"""

import sys
import os

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app, db_manager

def create_admin_user():
    """创建初始管理员用户"""
    # 连接数据库
    if not db_manager.connect():
        print("无法连接到数据库")
        return False
    
    # 创建管理员用户
    username = "admin"
    password = "admin123"
    
    success = db_manager.add_user(username, password, role="admin")
    db_manager.close()
    
    if success:
        print(f"管理员用户创建成功: 用户名={username}, 密码={password}")
        return True
    else:
        print("管理员用户创建失败")
        return False

if __name__ == "__main__":
    create_admin_user()