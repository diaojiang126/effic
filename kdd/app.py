import os
import uuid
import zipfile
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, session, redirect, url_for

from config import UPLOAD_FOLDER, EXPORT_FOLDER, MAX_CONTENT_LENGTH
from database import (
    insert_record, get_all_records, get_record_by_id, delete_record, update_record,
    get_custom_options, add_custom_option, delete_custom_option,
    verify_user, get_user_by_username, create_user, get_all_users, delete_user, init_db,
    delete_records_by_month, get_all_image_paths, get_records_page, apply_option_suggestion,
    normalize_date_range, get_records_by_express_date
)
from ocr_engine import allowed_file, parse_express_info
from metrics import METRICS

app = Flask(__name__)
app.secret_key = 'kdd_express_secret_key_2024'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['EXPORT_FOLDER'] = EXPORT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

os.makedirs(EXPORT_FOLDER, exist_ok=True)

init_db()

@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'message': '资源不存在'}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'success': False, 'message': '服务器内部错误'}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    traceback.print_exc()
    return jsonify({'success': False, 'message': f'请求处理错误: {str(e)}'}), 500

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        if session.get('role') != 'admin':
            return jsonify({'success': False, 'message': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated_function

def _export_date_range():
    """读取并校验导出日期范围，返回 (start, end)；非法时抛 ValueError。"""
    return normalize_date_range(
        request.args.get('start_date', ''),
        request.args.get('end_date', ''),
    )

def _export_filename(range_prefix, legacy_prefix, extension, start_date, end_date):
    """指定了完整日期范围时用「前缀_YYYYMMDD-YYYYMMDD」，否则沿用原时间戳命名。"""
    if start_date and end_date:
        span = f'{start_date.replace("-", "")}-{end_date.replace("-", "")}'
        return f'{range_prefix}_{span}{extension}'
    return f'{legacy_prefix}_{datetime.now().strftime("%Y%m%d%H%M%S")}{extension}'

@app.route('/')
def index():
    if 'user_id' in session:
        return render_template('index.html', username=session.get('username'), role=session.get('role'))
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username', '')
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'message': '请输入用户名和密码'})

    user = verify_user(username, password)
    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        return jsonify({'success': True, 'message': '登录成功', 'role': user['role']})
    else:
        return jsonify({'success': False, 'message': '用户名或密码错误'})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': '已退出登录'})

@app.route('/users')
@admin_required
def users_page():
    return render_template('users.html')

@app.route('/api/check_login', methods=['GET'])
def check_login():
    if 'user_id' in session:
        return jsonify({'success': True, 'logged_in': True, 'username': session.get('username'), 'role': session.get('role')})
    return jsonify({'success': True, 'logged_in': False})

@app.route('/api/upload', methods=['POST'])
@login_required
def upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'message': '不支持的文件格式'}), 400

    filename = str(uuid.uuid4()) + '.' + file.filename.rsplit('.', 1)[1].lower()
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    file.seek(0)
    file.save(filepath)

    try:
        ocr_result = parse_express_info(filepath)
        apply_option_suggestion(ocr_result)
        ocr_result['image_path'] = filename
        record_id = insert_record(ocr_result, session.get('username', ''))
        ocr_result['id'] = record_id
        return jsonify({'success': True, 'data': ocr_result})
    except Exception as e:
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'success': False, 'message': f'识别失败: {str(e)}'}), 500

@app.route('/api/upload/batch', methods=['POST'])
@login_required
def upload_batch():
    if 'files' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'}), 400

    files = request.files.getlist('files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'success': False, 'message': '没有选择文件'}), 400

    results = []
    errors = []

    for file in files:
        if file.filename == '' or not allowed_file(file.filename):
            continue

        filename = str(uuid.uuid4()) + '.' + file.filename.rsplit('.', 1)[1].lower()
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            ocr_result = parse_express_info(filepath)
            apply_option_suggestion(ocr_result)
            ocr_result['image_path'] = filename
            record_id = insert_record(ocr_result, session.get('username', ''))
            ocr_result['id'] = record_id
            results.append(ocr_result)
        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            errors.append({'filename': file.filename, 'error': str(e)})

    return jsonify({
        'success': True,
        'data': results,
        'errors': errors,
        'total': len(results),
        'failed': len(errors)
    })

@app.route('/api/records', methods=['GET'])
@login_required
def list_records():
    # limit 缺省时返回全部（保持旧行为）；slim=1 时不下发 full_text 大字段
    limit = request.args.get('limit', type=int)
    if limit is not None and limit <= 0:
        limit = None
    if limit:
        limit = min(limit, 500)
    offset = request.args.get('offset', type=int) or 0
    slim = request.args.get('slim') == '1'
    records, total = get_records_page(limit=limit, offset=offset, slim=slim)
    return jsonify({'success': True, 'data': records, 'total': total})

@app.route('/api/records/<int:record_id>', methods=['GET'])
@login_required
def get_record(record_id):
    record = get_record_by_id(record_id)
    if record:
        return jsonify({'success': True, 'data': record})
    return jsonify({'success': False, 'message': '记录不存在'}), 404

@app.route('/api/records/<int:record_id>', methods=['PUT'])
@login_required
def modify_record(record_id):
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'success': False, 'message': '请求数据无效'}), 400

        record = get_record_by_id(record_id)
        if not record:
            return jsonify({'success': False, 'message': '记录不存在'}), 404
        
        update_record(record_id, data)
        return jsonify({'success': True, 'message': '更新成功'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'更新失败: {str(e)}'}), 500

@app.route('/api/records/<int:record_id>', methods=['DELETE'])
@login_required
def remove_record(record_id):
    record = get_record_by_id(record_id)
    if not record:
        return jsonify({'success': False, 'message': '记录不存在'}), 404

    if record['image_path']:
        img_path = os.path.join(app.config['UPLOAD_FOLDER'], record['image_path'])
        if os.path.exists(img_path):
            os.remove(img_path)

    delete_record(record_id)
    return jsonify({'success': True, 'message': '删除成功'})

@app.route('/api/records/delete_by_month', methods=['POST'])
@admin_required
def delete_by_month():
    data = request.get_json()
    year = data.get('year')
    month = data.get('month')

    if not year or not month:
        return jsonify({'success': False, 'message': '请选择年月'}), 400

    from database import get_record_ids_by_month
    records_to_delete = get_record_ids_by_month(year, month)

    for record_id, image_path in records_to_delete:
        if image_path:
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], image_path)
            if os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except:
                    pass

    deleted_count = delete_records_by_month(year, month)
    return jsonify({'success': True, 'message': f'已删除 {deleted_count} 条记录'})

@app.route('/api/options/<option_type>', methods=['GET'])
@admin_required
def get_options(option_type):
    options = get_custom_options(option_type)
    return jsonify({'success': True, 'data': options})

@app.route('/api/options/<option_type>', methods=['POST'])
@admin_required
def add_option(option_type):
    data = request.get_json()
    option_value = data.get('value', '')
    if add_custom_option(option_type, option_value):
        return jsonify({'success': True, 'message': '添加成功'})
    return jsonify({'success': False, 'message': '添加失败'}), 400

@app.route('/api/options/<option_type>/<option_value>', methods=['DELETE'])
@admin_required
def remove_option(option_type, option_value):
    from database import delete_custom_option
    delete_custom_option(option_type, option_value)
    return jsonify({'success': True, 'message': '删除成功'})

@app.route('/api/users', methods=['GET'])
@admin_required
def list_users():
    users = get_all_users()
    return jsonify({'success': True, 'data': users})

@app.route('/api/users', methods=['POST'])
@admin_required
def add_user():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'user')

    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'}), 400

    if create_user(username, password, role):
        return jsonify({'success': True, 'message': '用户创建成功'})
    return jsonify({'success': False, 'message': '用户名已存在'}), 400

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user_api(user_id):
    if session.get('user_id') == user_id:
        return jsonify({'success': False, 'message': '不能删除当前登录用户'}), 400
    delete_user(user_id)
    return jsonify({'success': True, 'message': '删除成功'})

@app.route('/api/users/<int:user_id>/password', methods=['PUT'])
@admin_required
def update_user_password_api(user_id):
    data = request.get_json()
    new_password = data.get('password', '')
    
    if not new_password:
        return jsonify({'success': False, 'message': '密码不能为空'}), 400
    
    update_user_password(user_id, new_password)
    return jsonify({'success': True, 'message': '密码重置成功'})

@app.route('/api/export/excel', methods=['GET'])
@login_required
def export_excel():
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    try:
        start_date, end_date = _export_date_range()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    records = get_records_by_express_date(start_date, end_date)
    if not records:
        return jsonify({'success': False, 'message': '所选范围内没有数据'}), 400

    wb = Workbook()
    ws = wb.active
    ws.title = "快递单信息"

    headers = ['ID', '单号', '发件人', '发件地址', '收件人', '收件地址', '金额', '日期', '项目', '类型', '创建时间', '图片', '录入人']
    ws.append(headers)

    ws.column_dimensions['A'].width = 6
    ws.column_dimensions['B'].width = 22
    ws.column_dimensions['C'].width = 10
    ws.column_dimensions['D'].width = 40
    ws.column_dimensions['E'].width = 10
    ws.column_dimensions['F'].width = 40
    ws.column_dimensions['G'].width = 10
    ws.column_dimensions['H'].width = 12
    ws.column_dimensions['I'].width = 12
    ws.column_dimensions['J'].width = 12
    ws.column_dimensions['K'].width = 18
    ws.column_dimensions['L'].width = 10
    ws.column_dimensions['M'].width = 25

    for r in records:
        row_data = [
            r.get('id', ''),
            r.get('express_no', ''),
            r.get('sender_name', ''),
            r.get('sender_address', ''),
            r.get('receiver_name', ''),
            r.get('receiver_address', ''),
            r.get('amount', ''),
            r.get('express_date', ''),
            r.get('project', ''),
            r.get('record_type', ''),
            r.get('created_at', ''),
            '',
            r.get('created_by', '')
        ]
        ws.append(row_data)
        current_row = ws.max_row

        image_path = r.get('image_path') or ''
        if image_path:
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], image_path)
            if os.path.exists(img_path):
                try:
                    # 直接嵌入原始文件字节，不做解码/重编码，保证导出画质与分辨率与原图一致
                    ImgObj = XLImage(img_path)
                    img_width = ImgObj.width or 1
                    img_height = ImgObj.height or 1

                    display_width = 70
                    ImgObj.width = display_width
                    ImgObj.height = int(img_height * (display_width / img_width))

                    ws.add_image(ImgObj, f'L{current_row}')

                    ws.column_dimensions['L'].width = 25
                    ws.row_dimensions[current_row].height = ImgObj.height + 5
                except Exception as e:
                    print(f"Error adding image: {e}")

    export_file = _export_filename('快递单导出', '快递单记录', '.xlsx', start_date, end_date)
    export_path = os.path.join(app.config['EXPORT_FOLDER'], export_file)
    wb.save(export_path)

    return send_file(export_path, as_attachment=True, download_name=export_file)

@app.route('/api/export/csv', methods=['GET'])
@login_required
def export_csv():
    import pandas as pd

    try:
        start_date, end_date = _export_date_range()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    records = get_records_by_express_date(start_date, end_date)
    if not records:
        return jsonify({'success': False, 'message': '所选范围内没有数据'}), 400

    df = pd.DataFrame(records)
    df = df[['id', 'express_no', 'sender_name', 'sender_address',
             'receiver_name', 'receiver_address',
             'amount', 'express_date', 'project', 'record_type', 'created_at', 'image_path', 'created_by']]
    df.columns = ['ID', '单号', '发件人', '发件地址',
                  '收件人', '收件地址', '金额', '日期', '项目', '类型', '创建时间', '图片', '录入人']

    export_file = _export_filename('快递单导出', '快递单记录', '.csv', start_date, end_date)
    export_path = os.path.join(app.config['EXPORT_FOLDER'], export_file)
    df.to_csv(export_path, index=False, encoding='utf-8-sig')

    return send_file(export_path, mimetype='text/csv', as_attachment=True, download_name=export_file)

@app.route('/api/export/images', methods=['GET'])
@login_required
def export_images():
    try:
        start_date, end_date = _export_date_range()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    records = get_records_by_express_date(start_date, end_date)
    if not records:
        return jsonify({'success': False, 'message': '所选范围内没有数据'}), 400

    zip_filename = _export_filename('快递单导出', '快递单图片', '.zip', start_date, end_date)
    zip_path = os.path.join(app.config['EXPORT_FOLDER'], zip_filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for record in records:
            if record['image_path']:
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], record['image_path'])
                if os.path.exists(img_path):
                    arcname = f"{record['express_no'] or record['id']}_{record['image_path']}"
                    zipf.write(img_path, arcname)

    return send_file(zip_path, as_attachment=True, download_name=zip_filename)

@app.route('/uploads/<filename>')
@login_required
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/metrics', methods=['GET'])
@admin_required
def api_metrics():
    return jsonify({'success': True, 'data': METRICS.snapshot()})

@app.route('/metrics')
@admin_required
def metrics_page():
    return render_template('metrics.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5012)