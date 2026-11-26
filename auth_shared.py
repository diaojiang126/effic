from flask import request, jsonify, session
import functools
import json
import traceback
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

def require_auth(f):
    """支持 JWT token 认证或 session 登录的装饰器"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            try:
                verify_jwt_in_request()
                identity_str = get_jwt_identity()
                identity = json.loads(identity_str)
                if identity:
                    request.user_info = {
                        'user_id': identity.get('user_id'),
                        'username': identity.get('username'),
                        'role': identity.get('role'),
                        'company_id': identity.get('company_id')
                    }
                    session['user_id'] = identity.get('user_id')
                    session['username'] = identity.get('username')
                    session['role'] = identity.get('role')
                    session['company_id'] = identity.get('company_id')
                    return f(*args, **kwargs)
            except Exception as e:
                traceback.print_exc()
                print(f"require_auth JWT验证失败: {e}")
            return jsonify({'success': False, 'message': '未登录或token无效'}), 401
        elif 'user_id' in session:
            return f(*args, **kwargs)
        else:
            return jsonify({'success': False, 'message': '未登录'}), 401
    return decorated
