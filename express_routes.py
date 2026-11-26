from flask import Blueprint, render_template, request, jsonify, session, send_file, send_from_directory, current_app
from functools import wraps
import os
import uuid
import csv
from datetime import datetime
from io import BytesIO, StringIO
import zipfile
import json
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.utils import get_column_letter

from kdd import database as express_db
from kdd.ocr_engine import parse_express_info, allowed_file as express_allowed_file
from kdd.config import UPLOAD_FOLDER
from kdd.metrics import METRICS
from auth_shared import require_auth
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

def _get_image_path(image_path):
    if not image_path:
        return ''
    basename = os.path.basename(image_path)
    return os.path.join(UPLOAD_FOLDER, basename)

def _remove_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

def _export_date_range():
    """读取并校验导出日期范围，返回 (start, end)；非法时抛 ValueError。"""
    return express_db.normalize_date_range(
        request.args.get('start_date', ''),
        request.args.get('end_date', ''),
    )

def _export_filename(range_prefix, legacy_prefix, extension, start_date, end_date):
    """指定了完整日期范围时用「前缀_YYYYMMDD-YYYYMMDD」，否则沿用原时间戳命名。"""
    if start_date and end_date:
        span = f'{start_date.replace("-", "")}-{end_date.replace("-", "")}'
        return f'{range_prefix}_{span}{extension}'
    return f'{legacy_prefix}_{datetime.now().strftime("%Y%m%d_%H%M%S")}{extension}'

def _rename_image(old_path, express_no, project, record_type):
    dirname = os.path.dirname(old_path)
    ext = os.path.splitext(old_path)[1]
    parts = [p for p in [express_no, project, record_type] if p]
    if parts:
        sanitized = []
        for p in parts:
            p = str(p).strip().replace('/', '_').replace('\\', '_')
            p = ''.join(c for c in p if c not in ':*?"<>|')
            sanitized.append(p)
        base = '_'.join(sanitized)
    else:
        base = os.path.splitext(os.path.basename(old_path))[0]
    new_name = f"{base}{ext}"
    new_path = os.path.join(dirname, new_name)
    if os.path.exists(new_path):
        counter = 1
        while True:
            new_name = f"{base}_{counter}{ext}"
            new_path = os.path.join(dirname, new_name)
            if not os.path.exists(new_path):
                break
            counter += 1
    os.rename(old_path, new_path)
    return new_name

express_bp = Blueprint('express', __name__, url_prefix='/express')

def login_required_express(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            auth_header = request.headers.get('Authorization')
            if auth_header and auth_header.startswith('Bearer '):
                try:
                    verify_jwt_in_request()
                    identity = json.loads(get_jwt_identity())
                    if identity:
                        session['user_id'] = identity.get('user_id')
                        session['username'] = identity.get('username')
                        session['role'] = identity.get('role')
                        session['company_id'] = identity.get('company_id')
                        return f(*args, **kwargs)
                except Exception as e:
                    print(f"login_required_express JWT验证失败: {e}")
                    import traceback
                    traceback.print_exc()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
                return jsonify({'success': False, 'message': '请先登录！'})
            return jsonify({'success': False, 'message': '请先登录！'})
        return f(*args, **kwargs)
    return decorated_function

@express_bp.route('/')
@login_required_express
def index():
    return render_template('express_index.html',
                         username=session.get('username', ''),
                         role=session.get('role', ''))

@express_bp.route('/api/upload', methods=['POST'])
@require_auth
def upload_file():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有上传文件'})
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'})
    if file and express_allowed_file(file.filename):
        # 客户端幂等键：弱网重试时避免同一次上传产生重复记录
        client_token = (request.form.get('client_token') or '').strip()
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4()}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        current_path = filepath
        try:
            result = parse_express_info(filepath)
            express_db.apply_option_suggestion(result)
            new_name = _rename_image(filepath,
                result.get('express_no', ''),
                result.get('project', ''),
                result.get('record_type', ''))
            current_path = os.path.join(UPLOAD_FOLDER, new_name)
            result['image_path'] = new_name
            record_id, created = express_db.insert_record_once(
                result, session.get('username', ''), client_token, status='draft')
            if not created:
                # 重复提交：删掉刚保存的冗余图片，直接返回已存在的记录
                _remove_file(current_path)
                existing = express_db.get_record_by_id(record_id)
                if existing:
                    return jsonify({'success': True, 'data': existing})
            result['id'] = record_id
            return jsonify({'success': True, 'data': result})
        except Exception as e:
            _remove_file(current_path)
            return jsonify({'success': False, 'message': f'识别失败: {str(e)}'})
    return jsonify({'success': False, 'message': '不支持的文件格式'})

@express_bp.route('/api/upload/batch', methods=['POST'])
@require_auth
def upload_batch():
    if 'files' not in request.files:
        return jsonify({'success': False, 'message': '没有上传文件'})
    files = request.files.getlist('files')
    if not files:
        return jsonify({'success': False, 'message': '没有选择文件'})
    results = []
    errors = []
    for file in files:
        if file and express_allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"{uuid.uuid4()}.{ext}"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)
            try:
                result = parse_express_info(filepath)
                express_db.apply_option_suggestion(result)
                new_name = _rename_image(filepath,
                    result.get('express_no', ''),
                    result.get('project', ''),
                    result.get('record_type', ''))
                result['image_path'] = new_name
                record_id = express_db.insert_record(
                    result, session.get('username', ''), status='draft')
                result['id'] = record_id
                results.append(result)
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

@express_bp.route('/api/records')
@require_auth
def get_records():
    try:
        # limit 缺省时返回全部（保持旧行为）；slim=1 时不下发 full_text 大字段
        limit = request.args.get('limit', type=int)
        if limit is not None and limit <= 0:
            limit = None
        if limit:
            limit = min(limit, 500)
        offset = request.args.get('offset', type=int) or 0
        slim = request.args.get('slim') == '1'
        records, total = express_db.get_records_page(limit=limit, offset=offset, slim=slim)
        return jsonify({'success': True, 'data': records, 'total': total})
    except Exception as e:
        print(f"Error loading records: {e}")
        return jsonify({'success': False, 'message': f'加载记录失败: {str(e)}'})

@express_bp.route('/api/records/<int:record_id>', methods=['GET'])
@require_auth
def get_record(record_id):
    record = express_db.get_record_by_id(record_id)
    if record:
        return jsonify({'success': True, 'data': record})
    return jsonify({'success': False, 'message': '记录不存在'})

@express_bp.route('/api/records/<int:record_id>', methods=['PUT'])
@require_auth
def update_record(record_id):
    data = request.get_json()
    express_db.update_record(record_id, data)
    return jsonify({'success': True, 'message': '更新成功'})

@express_bp.route('/api/records/finalize', methods=['POST'])
@require_auth
def finalize_records():
    """批量保存：上传识别后记录处于草稿状态，只有全部文件都填好项目与类型时才整体保存。"""
    data = request.get_json(silent=True) or {}
    records = data.get('records') or []
    try:
        saved = express_db.finalize_records(records)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except Exception as exc:
        return jsonify({'success': False, 'message': f'保存失败: {str(exc)}'}), 500
    return jsonify({'success': True, 'message': f'已保存{saved}条记录', 'saved': saved})

@express_bp.route('/api/records/<int:record_id>', methods=['DELETE'])
@require_auth
def delete_record(record_id):
    record = express_db.get_record_by_id(record_id)
    if record and record.get('image_path'):
        image_full_path = _get_image_path(record['image_path'])
        if os.path.exists(image_full_path):
            os.remove(image_full_path)
    express_db.delete_record(record_id)
    return jsonify({'success': True, 'message': '删除成功'})

@express_bp.route('/api/records/delete_by_month', methods=['POST'])
@require_auth
def delete_records_by_month():
    user_info = getattr(request, 'user_info', None) or {}
    if user_info.get('role') != 'admin' and session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '权限不足，需要管理员权限！'})
    data = request.get_json()
    year = data.get('year')
    month = data.get('month')
    if not year or not month:
        return jsonify({'success': False, 'message': '请选择年月'})
    records = express_db.get_record_ids_by_month(year, month)
    for record_id, image_path in records:
        if image_path:
            image_full_path = _get_image_path(image_path)
            if os.path.exists(image_full_path):
                os.remove(image_full_path)
    deleted_count = express_db.delete_records_by_month(year, month)
    return jsonify({'success': True, 'message': f'已删除{deleted_count}条记录'})

@express_bp.route('/api/options/<option_type>', methods=['GET'])
@require_auth
def get_options(option_type):
    options = express_db.get_custom_options(option_type)
    return jsonify({'success': True, 'data': options})

@express_bp.route('/api/options/<option_type>', methods=['POST'])
@require_auth
def add_option(option_type):
    data = request.get_json()
    value = data.get('value', '').strip()
    if not value:
        return jsonify({'success': False, 'message': '选项值不能为空'})
    success = express_db.add_custom_option(option_type, value)
    if success:
        return jsonify({'success': True, 'message': '添加成功'})
    return jsonify({'success': False, 'message': '添加失败，可能已存在'})

@express_bp.route('/api/options/<option_type>/<path:option_value>', methods=['DELETE'])
@require_auth
def delete_option(option_type, option_value):
    user_info = getattr(request, 'user_info', None) or {}
    if user_info.get('role') != 'admin' and session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '权限不足，需要管理员权限！'})
    express_db.delete_custom_option(option_type, option_value)
    return jsonify({'success': True, 'message': '删除成功'})

@express_bp.route('/api/export/excel')
@require_auth
def export_excel():
    try:
        start_date, end_date = _export_date_range()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    records = express_db.get_records_by_express_date(start_date, end_date)
    if not records:
        return jsonify({'success': False, 'message': '所选范围内没有数据可导出'}), 400

    wb = Workbook()
    ws = wb.active
    ws.title = '快递单记录'

    headers = ['ID', '快递单号', '发件人', '发件电话', '发件地址',
               '收件人', '收件电话', '收件地址', '金额', '日期',
               '项目', '类型', '录入人', '创建时间', '图片']

    header_font = Font(bold=True, size=11, color='FFFFFF')
    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_alignment = Alignment(horizontal='center', vertical='center')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    IMAGE_ROW_HEIGHT = 260
    IMAGE_DISPLAY_WIDTH = 350

    for row_idx, record in enumerate(records, 2):
        values = [
            record['id'],
            record.get('express_no', ''), record.get('sender_name', ''),
            record.get('sender_phone', ''), record.get('sender_address', ''),
            record.get('receiver_name', ''), record.get('receiver_phone', ''),
            record.get('receiver_address', ''), record.get('amount', ''),
            record.get('express_date', ''), record.get('project', ''),
            record.get('record_type', ''), record.get('created_by', ''),
            record.get('created_at', '')
        ]
        for col_idx, value in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center')

        image_path = record.get('image_path', '')
        if image_path:
            full_path = _get_image_path(image_path)
            if os.path.exists(full_path):
                try:
                    excel_img = ExcelImage(full_path)
                    ratio = IMAGE_DISPLAY_WIDTH / excel_img.width
                    excel_img.width = IMAGE_DISPLAY_WIDTH
                    excel_img.height = int(excel_img.height * ratio)
                    col_letter = get_column_letter(15)
                    ws.add_image(excel_img, f'{col_letter}{row_idx}')
                    ws.row_dimensions[row_idx].height = IMAGE_ROW_HEIGHT
                except Exception:
                    ws.cell(row=row_idx, column=15, value='图片加载失败')

    col_widths = [5, 18, 10, 14, 28, 10, 14, 28, 10, 12, 10, 8, 10, 18, 52]
    for col_idx, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.row_dimensions[1].height = 22
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = 'A2'

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    export_filename = _export_filename('快递单导出', '快递单记录', '.xlsx', start_date, end_date)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=export_filename
    )

@express_bp.route('/api/export/csv')
@require_auth
def export_csv():
    try:
        start_date, end_date = _export_date_range()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    records = express_db.get_records_by_express_date(start_date, end_date)
    if not records:
        return jsonify({'success': False, 'message': '所选范围内没有数据可导出'}), 400

    # csv.writer 需要文本流；此前直接写入 BytesIO 会抛 TypeError，导致 CSV 导出必然 500
    text_buffer = StringIO()
    writer = csv.writer(text_buffer)
    writer.writerow(['ID', '快递单号', '发件人', '发件电话', '发件地址',
                     '收件人', '收件电话', '收件地址', '金额', '日期',
                     '项目', '类型', '录入人', '创建时间'])
    for r in records:
        writer.writerow([
            r['id'], r.get('express_no', ''), r.get('sender_name', ''),
            r.get('sender_phone', ''), r.get('sender_address', ''),
            r.get('receiver_name', ''), r.get('receiver_phone', ''),
            r.get('receiver_address', ''), r.get('amount', ''),
            r.get('express_date', ''), r.get('project', ''),
            r.get('record_type', ''), r.get('created_by', ''),
            r.get('created_at', '')
        ])

    output = BytesIO()
    output.write('\ufeff'.encode('utf-8'))   # BOM，保证 Excel 正确识别中文
    output.write(text_buffer.getvalue().encode('utf-8'))
    output.seek(0)
    export_filename = _export_filename('快递单导出', '快递单记录', '.csv', start_date, end_date)
    return send_file(
        output,
        mimetype='text/csv',
        as_attachment=True,
        download_name=export_filename
    )

@express_bp.route('/api/export/images')
@require_auth
def export_images():
    try:
        start_date, end_date = _export_date_range()
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    records = express_db.get_records_by_express_date(start_date, end_date)
    if not records:
        return jsonify({'success': False, 'message': '所选范围内没有数据可导出'}), 400
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for r in records:
            image_path = r.get('image_path', '')
            if image_path:
                full_path = _get_image_path(image_path)
                if os.path.exists(full_path):
                    arcname = f"{r['id']}_{r.get('express_no', 'unknown')}.{image_path.rsplit('.', 1)[-1]}"
                    zf.write(full_path, arcname)
    zip_buffer.seek(0)
    export_filename = _export_filename('快递单导出', '快递单图片', '.zip', start_date, end_date)
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=export_filename
    )

@express_bp.route('/uploads/<path:filename>')
@require_auth
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, os.path.basename(filename))

@express_bp.route('/api/metrics')
@require_auth
def get_metrics():
    user_info = getattr(request, 'user_info', None) or {}
    if user_info.get('role') != 'admin' and session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '权限不足，需要管理员权限！'})
    return jsonify({'success': True, 'data': METRICS.snapshot()})
