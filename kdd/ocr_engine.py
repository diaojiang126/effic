"""快递单识别引擎：图像预处理 -> 带坐标的高精度 OCR -> 版面分栏 -> 字段结构化。

相比旧版的三点关键变化：
1. 送识别前先做图像质量治理（见 image_enhance.py）；
2. 改用带文字坐标的高精度接口，按左右分栏 / 上下分块定位寄收件信息，
   不再依赖"地址出现顺序"猜测；
3. 补齐电话号码抽取，日期识别不到时返回空值而不再伪造当天日期。
"""
import re
import time

from aip import AipOcr

from config import BAIDU_AIP_APP_ID, BAIDU_AIP_API_KEY, BAIDU_AIP_SECRET_KEY
from image_enhance import preprocess_image
from metrics import METRICS

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'gif'}

# 按优先级尝试，越靠前准确率越高且返回文字坐标
OCR_METHODS = ('accurate', 'basicAccurate', 'general', 'basicGeneral')

SENDER_KEYWORDS = ('寄件人', '发件人', '发货人', '寄件', '发件')
RECEIVER_KEYWORDS = ('收件人', '收货人', '收件', '收货')

# 行内出现这些词说明是字段标签而非寄/收件信息锚点（如「寄件时间」「寄方费用合计」）
ANCHOR_BLOCKLIST = ('时间', '费用', '合计', '备注', '底单', '详情', '图片', '方式', '类型', '计费', '重量', '数量')

SENDER_NAME_KEYWORDS = ('寄件人', '发件人', '发货人')
RECEIVER_NAME_KEYWORDS = ('收件人', '收货人')

SENDER_ADDR_KEYWORDS = ('寄件地址', '发件地址', '发货地址', '寄件人地址', '发件人地址', '寄件地址')
RECEIVER_ADDR_KEYWORDS = ('收件地址', '收货地址', '收件人地址', '收货人地址', '送货地址', '邮寄地址')

NO_KEYWORDS = ('快递单号', '运单号', '货运单号', '订单编号', '订单号', '包裹号', '流水号', '单号', '编号')

NAME_STOP_WORDS = {'信息', '姓名', '电话', '手机', '地址', '公司', '名称', '联系人', '单位', '收件', '寄件'}

# 「姓名+电话」兜底匹配时，含这些字的候选几乎都是地址/方位碎片而非人名
NAME_REJECT_WORDS = ('省', '市', '区', '县', '镇', '乡', '村', '街', '路', '道', '巷', '号', '幢', '栋',
                     '座', '楼', '室', '园', '苑', '城', '大厦', '小区', '附近', '左右', '方向',
                     '交叉口', '门面', '便利店', '层', '库', '店', '的', '口')

ADDRESS_STOP_WORDS = ('电话', '手机', '姓名', '联系人', '运单号', '单号', '签收', '发货人', '寄件人', '收件人')

# 地址行的典型特征字，用于判断折行是否仍属于同一条地址
ADDRESS_HINTS = ('省', '市', '区', '县', '镇', '乡', '村', '街', '路', '道', '巷', '号',
                 '幢', '栋', '座', '楼', '室', '单元', '组', '园', '苑', '城', '大厦', '小区')

PROVINCES = (
    '北京', '天津', '河北', '山西', '内蒙古', '辽宁', '吉林', '黑龙江', '上海', '江苏', '浙江',
    '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南', '广东', '广西', '海南', '重庆',
    '四川', '贵州', '云南', '西藏', '陕西', '甘肃', '青海', '宁夏', '新疆', '香港', '澳门', '台湾',
)

MOBILE_RE = re.compile(r'(?<!\d)(1[3-9]\d{9})(?!\d)')
LANDLINE_RE = re.compile(r'(?<!\d)(0\d{2,3}[-\s]?\d{7,8})(?!\d)')
# 允许 OCR 把手机号中的数字识别成字母，纠正后再校验
MOBILE_LOOSE_RE = re.compile(r'(?<!\d)(1[3-9][0-9OoIlDSBGZ]{9})(?!\d)')
# 电话形态：手机 / 打码手机 / 座机 / 打码座机
PHONE_PATTERN = r'(?:1[3-9]\d{9}|1[3-9]\d[\d*]{6,9}|0\d{2,3}[-\s]?\d{7,8}|0\d{2,3}[-\s]?[\d*]{6,9})'
# 打码电话，如 137**5746、021***3288（顺丰电子面单常见）
PHONE_MASKED_RE = re.compile(r'(?<!\d)(' + PHONE_PATTERN + r')(?!\d)')
# 姓名与电话紧邻，允许中间夹一个短括号后缀，如「蒋丹军137**5746」「董海红（售后）158**0525」
PERSON_PHONE_RE = re.compile(
    r'([\u4e00-\u9fa5]{2,4})(?:[（(][^）)]{1,6}[）)])?\s*(' + PHONE_PATTERN + r')')

# OCR 易混字符。仅在"替换后能构成合法值"时才采纳，避免破坏本身就正确的内容。
DIGIT_CONFUSIONS = (
    ('O', '0'), ('o', '0'), ('D', '0'),
    ('I', '1'), ('l', '1'), ('i', '1'),
    ('Z', '2'), ('S', '5'), ('B', '8'), ('G', '6'),
)

# 末尾的 (?!\d) 防止在数字连排时截断出错误单号
CARRIER_NO_PATTERNS = (
    r'(SF\d{12,15})(?!\d)',
    r'(YT\d{12,15})(?!\d)',
    r'(STO\d{12,15})(?!\d)',
    r'(ZTO\d{12,15})(?!\d)',
    r'(YTO\d{12,15})(?!\d)',
    r'(YD\d{12,15})(?!\d)',
    r'(JD[A-Z]{0,2}\d{10,15})(?!\d)',
    r'(EMS\d{9,13})(?!\d)',
    r'(DB\d{10,15})(?!\d)',
    r'(GTO\d{12,15})(?!\d)',
    r'(ZJS\d{10,15})(?!\d)',
)

DATE_PATTERNS = (
    r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)',
    r'(\d{4}\.\d{1,2}\.\d{1,2})',
    r'(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)',
    r'(\d{2}[/年\-\.]\d{1,2}[/月\-\.]\d{1,2})',
)

DATE_KEYWORDS = ('下单日期', '寄件日期', '收件日期', '创建时间', '下单时间', '日期', '时间')

CLIENT = None


def get_ocr_client():
    global CLIENT
    if CLIENT is None:
        CLIENT = AipOcr(BAIDU_AIP_APP_ID, BAIDU_AIP_API_KEY, BAIDU_AIP_SECRET_KEY)
    return CLIENT


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _run_ocr(client, image_bytes):
    """优先使用带坐标的高精度接口，失败时逐级降级。"""
    last_error = None
    for method_name in OCR_METHODS:
        method = getattr(client, method_name, None)
        if method is None:
            continue
        try:
            result = method(image_bytes)
        except Exception as exc:
            last_error = exc
            continue
        if isinstance(result, dict) and result.get('words_result') is not None:
            return result
        last_error = result
    raise RuntimeError(f'OCR 接口调用失败: {last_error}')


def _extract_ocr_lines(result):
    """把 OCR 结果整理成带坐标的文本行，按阅读顺序排序。"""
    lines = []
    for item in result.get('words_result') or []:
        words = (item.get('words') or '').strip()
        if not words:
            continue
        location = item.get('location') or {}
        lines.append({
            'words': words,
            'left': float(location.get('left') or 0),
            'top': float(location.get('top') or 0),
            'width': float(location.get('width') or 0),
            'height': float(location.get('height') or 0),
        })
    lines.sort(key=lambda x: (x['top'], x['left']))
    return lines


def _center_x(line):
    return line['left'] + line['width'] / 2.0


def _is_anchor_line(words, keywords):
    """是否为寄/收件信息锚点行，排除「寄件时间」这类字段标签。"""
    if any(word in words for word in ANCHOR_BLOCKLIST):
        return False
    return any(keyword in words for keyword in keywords)


def _median_center_x(lines, keywords):
    xs = sorted(_center_x(line) for line in lines if _is_anchor_line(line['words'], keywords))
    if not xs:
        return None
    return xs[len(xs) // 2]


def _split_by_columns(lines, image_width):
    """左右分栏：寄件信息在左、收件信息在右的运单。"""
    sender_x = _median_center_x(lines, SENDER_KEYWORDS)
    receiver_x = _median_center_x(lines, RECEIVER_KEYWORDS)
    if sender_x is None or receiver_x is None:
        return None
    if abs(sender_x - receiver_x) < max(80.0, image_width * 0.18):
        return None
    middle = (sender_x + receiver_x) / 2.0
    return {
        'sender': [line for line in lines if _center_x(line) <= middle],
        'receiver': [line for line in lines if _center_x(line) > middle],
    }


def _marker_region(words):
    """识别单字图标标记（电子面单里的「寄」「收」）。"""
    text = (words or '').strip()
    if text in ('寄', '发'):
        return 'sender'
    if text == '收':
        return 'receiver'
    if len(text) > 1 and text[1] in ' \t:：':
        if text[0] in '寄发':
            return 'sender'
        if text[0] == '收':
            return 'receiver'
    return None


def _split_by_markers(lines):
    """按单字「寄」「收」标记的垂直位置就近分配文本行。

    电子面单常见版式：标记与「姓名 + 地址」块的垂直中心对齐，且寄/收先后顺序
    并不固定，因此按"离哪个标记更近"归属，而不是按出现顺序切分。
    """
    marker_tops = {}
    for line in lines:
        region = _marker_region(line['words'])
        if region and region not in marker_tops:
            marker_tops[region] = line['top']

    if 'sender' not in marker_tops or 'receiver' not in marker_tops:
        return None
    if abs(marker_tops['sender'] - marker_tops['receiver']) < 40:
        return None

    regions = {'sender': [], 'receiver': []}
    for line in lines:
        to_sender = abs(line['top'] - marker_tops['sender'])
        to_receiver = abs(line['top'] - marker_tops['receiver'])
        regions['sender' if to_sender <= to_receiver else 'receiver'].append(line)
    return regions


def _split_by_sections(lines):
    """上下布局：以寄件/收件关键词首次出现的行作为分界。"""
    anchors = []
    for index, line in enumerate(lines):
        if _is_anchor_line(line['words'], RECEIVER_KEYWORDS):
            anchors.append((index, 'receiver'))
            break
    for index, line in enumerate(lines):
        if _is_anchor_line(line['words'], SENDER_KEYWORDS):
            anchors.append((index, 'sender'))
            break
    if not anchors:
        return None
    anchors.sort()
    first_index, first_type = anchors[0]
    if len(anchors) == 1:
        return {first_type: lines[first_index:], _other(first_type): lines[:first_index]}
    second_index, second_type = anchors[1]
    return {
        first_type: lines[first_index:second_index],
        second_type: lines[second_index:],
    }


def _other(section_type):
    return 'receiver' if section_type == 'sender' else 'sender'


def _split_regions(lines, image_width):
    """依次尝试左右分栏、单字图标标记、上下分块。

    返回 (寄件行, 收件行, 是否成功区分)。全部失败时返回 (lines, lines, False)，
    由调用方按出现顺序做兜底分配，避免寄收双方取到同一条记录。
    """
    if not lines:
        return lines, lines, False

    candidates = (
        _split_by_columns(lines, image_width or 0),
        _split_by_markers(lines),
        _split_by_sections(lines),
    )
    for regions in candidates:
        if regions and regions.get('sender') and regions.get('receiver'):
            return regions['sender'], regions['receiver'], True
    return lines, lines, False


def _is_plausible_person_name(name):
    if not name or name in NAME_STOP_WORDS:
        return False
    return not any(word in name for word in NAME_REJECT_WORDS)


def _extract_person_name(text, keywords):
    for keyword in keywords:
        for match in re.finditer(keyword + r'[：:\s]*([\u4e00-\u9fa5·]{2,10})', text):
            name = match.group(1)
            if name not in NAME_STOP_WORDS:
                return name, 'high'
    # 无「寄件人/收件人」字样时（电子面单只用「寄」「收」图标），退化为「姓名+电话」
    for match in PERSON_PHONE_RE.finditer(text):
        if _is_plausible_person_name(match.group(1)):
            return match.group(1), 'medium'
    return '', 'low'


def _extract_ordered_persons(text):
    """按出现顺序返回「姓名, 电话」对，用于无法区分寄收区域时按序分配。"""
    results = []
    for match in PERSON_PHONE_RE.finditer(text or ''):
        if _is_plausible_person_name(match.group(1)):
            results.append((match.group(1), match.group(2)))
    return results


def _extract_ordered_addresses(text):
    """按出现顺序返回候选地址（按行截断），用于无法区分寄收区域时按序分配。"""
    results = []
    pattern = r'(?:' + '|'.join(PROVINCES) + r')[\u4e00-\u9fa5A-Za-z0-9\-#]{4,}'
    for match in re.finditer(pattern, text or ''):
        address = _clean_address(match.group(0))
        if address and address not in results:
            results.append(address)
    return results


def _confidence(value):
    return 'medium' if value else 'low'


def _normalize_digits(text):
    """把 OCR 常见的字母/数字混淆字符替换为数字。"""
    result = text
    for source, target in DIGIT_CONFUSIONS:
        result = result.replace(source, target)
    return result


def _extract_phone(text):
    match = MOBILE_RE.search(text)
    if match:
        return match.group(1), 'high'
    # 含字母混淆的手机号：纠正后必须仍是合法手机号才采纳，避免误取
    loose = MOBILE_LOOSE_RE.search(text)
    if loose:
        fixed = _normalize_digits(loose.group(1))
        if MOBILE_RE.fullmatch(fixed):
            return fixed, 'high'
    # 打码电话（手机或座机，如 137**5746 / 021***3288）：本身不完整，仅作参考
    for masked in PHONE_MASKED_RE.finditer(text):
        if '*' in masked.group(1):
            return masked.group(1), 'medium'
    match = LANDLINE_RE.search(text)
    if match:
        return match.group(1), 'medium'
    return '', 'low'


def _clean_address(raw):
    """拼接跨行地址并截断到下一个字段，去掉非地址字符。"""
    text = re.sub(r'\s+', '', raw or '')
    for stop in ADDRESS_STOP_WORDS:
        index = text.find(stop)
        if index > 0:
            text = text[:index]
    text = re.sub(r'[^\u4e00-\u9fa5A-Za-z0-9\-#]', '', text)
    return text[:80] if len(text) >= 6 else ''


def _looks_like_address(text):
    """判断某一行是否可能是地址（用于决定折行地址是否继续拼接）。"""
    line = (text or '').strip()
    if len(line) < 3:
        return False
    if PERSON_PHONE_RE.search(line):
        return False
    return any(hint in line for hint in ADDRESS_HINTS)


def _extract_address(text, keywords):
    """抽取地址：关键词定位优先，其次按行政区划种子；折行按地址特征收口。

    只接纳"看起来仍是地址"的后续行，避免把「托寄物」「数量」等下一个字段吞进来。
    """
    lines = [line.strip() for line in (text or '').split('\n') if line.strip()]

    def collect(start_index, first_piece):
        pieces = [first_piece] if first_piece else []
        for following in lines[start_index + 1:]:
            if not _looks_like_address(following):
                break
            pieces.append(following)
        return _clean_address(''.join(pieces))

    for keyword in keywords:
        for index, line in enumerate(lines):
            position = line.find(keyword)
            if position < 0:
                continue
            address = collect(index, line[position + len(keyword):].lstrip('：: '))
            if address:
                return address, 'high'

    for index, line in enumerate(lines):
        if not any(province in line for province in PROVINCES):
            continue
        address = collect(index, line)
        if address:
            return address, 'medium'
    return '', 'low'


def _is_phone_like(value):
    return bool(MOBILE_RE.fullmatch(value) or LANDLINE_RE.fullmatch(value))


def _normalize_express_no(value):
    """修正单号主体里的字母/数字混淆：仅当修正后主体为纯数字时才采纳。"""
    text = (value or '').strip().upper()
    match = re.match(r'^([A-Z]{1,4})([A-Z0-9]{6,})$', text)
    if not match:
        return text
    prefix, body = match.group(1), match.group(2)
    if body.isdigit():
        return text
    fixed = _normalize_digits(body)
    return prefix + fixed if fixed.isdigit() else text


def _extract_express_no(full_text):
    text = full_text or ''
    flat = re.sub(r'\s+', '', text)

    for keyword in NO_KEYWORDS:
        for match in re.finditer(keyword + r'[：:]?', text):
            # 只在同一行内取单号，避免拼上下一行的日期/电话
            tail = re.sub(r'\s+', '', text[match.end():match.end() + 30].split('\n')[0])
            candidate = re.match(r'([A-Za-z0-9]{8,30})', tail)
            if candidate:
                value = _normalize_express_no(candidate.group(1))
                if not _is_phone_like(value):
                    return value, 'high'

    for pattern in CARRIER_NO_PATTERNS:
        match = re.search(pattern, flat, re.IGNORECASE)
        if match:
            return _normalize_express_no(match.group(1)), 'high'

    match = re.search(r'(?<!\d)(\d{10,20})(?!\d)', flat)
    if match and not _is_phone_like(match.group(1)):
        return match.group(1), 'medium'

    return '', 'low'


# 金额取值：容忍 OCR 把小数点识别成逗号
_AMOUNT_VALUE = r'[¥￥]?\s*(\d+[.,]?\d{0,2})'


def extract_amount(text):
    amount_candidates = []

    patterns_with_label = [
        (r'金额[：:\s]*' + _AMOUNT_VALUE, 100),
        (r'实付[：:\s]*' + _AMOUNT_VALUE, 100),
        (r'总价[：:\s]*' + _AMOUNT_VALUE, 100),
        (r'支付[：:\s]*' + _AMOUNT_VALUE, 90),
        (r'付款[：:\s]*' + _AMOUNT_VALUE, 90),
        (r'邮费[：:\s]*' + _AMOUNT_VALUE, 80),
        (r'费用[：:\s]*' + _AMOUNT_VALUE, 80),
        (r'应付[：:\s]*' + _AMOUNT_VALUE, 90),
        (r'寄方费用合计[\s\S]{0,30}?' + _AMOUNT_VALUE, 95),
        (r'费用合计[\s\S]{0,30}?' + _AMOUNT_VALUE, 85),
        (r'合计[\s\S]{0,20}?' + _AMOUNT_VALUE, 80),
    ]

    for pattern, weight in patterns_with_label:
        for match in re.finditer(pattern, text):
            value = match.group(1).replace(',', '.')
            try:
                num = float(value)
                if 0 < num < 100000:
                    amount_candidates.append((value, weight))
            except ValueError:
                pass

    if amount_candidates:
        amount_candidates.sort(key=lambda x: (-x[1], -float(x[0])))
        return amount_candidates[0][0], 'high'

    # 货币符号后的金额：容忍 OCR 把小数点识别成逗号
    for match in re.finditer(r'[¥￥]\s*(\d+[.,]\d{2})', text):
        value = match.group(1).replace(',', '.')
        try:
            num = float(value)
            if 0 < num < 100000:
                return value, 'medium'
        except ValueError:
            pass

    return '', 'low'


def _build_date(year, month, day):
    """校验并规范化年月日，非法值返回空串。"""
    if len(year) == 2:
        year = '20' + year
    if len(year) != 4:
        return ''
    try:
        y, m, d = int(year), int(month), int(day)
    except ValueError:
        return ''
    if not (2000 <= y <= 2099 and 1 <= m <= 12 and 1 <= d <= 31):
        return ''
    return f'{y:04d}-{m:02d}-{d:02d}'


def _normalize_date(raw):
    text = re.sub(r'\s+', '', raw or '')
    text = re.sub(r'[年日月]', '-', text)
    text = text.replace('.', '-').replace('/', '-').rstrip('-')
    parts = [p for p in text.split('-') if p]
    if len(parts) < 3:
        return ''
    return _build_date(parts[0], parts[1], parts[2])


def extract_date(text):
    """识别不到日期时返回空值，不再用当天日期兜底。"""
    spaced = re.sub(r'(\d)([年月日])', r'\1 \2', text or '')
    spaced = re.sub(r'([年月日])\s*(\d)', r'\1 \2', spaced)

    for pattern in DATE_PATTERNS:
        match = re.search(pattern, spaced)
        if match:
            normalized = _normalize_date(match.group(0))
            if normalized:
                return normalized, 'high'

    for keyword in DATE_KEYWORDS:
        index = spaced.find(keyword)
        if index < 0:
            continue
        chunk = spaced[index:index + 30]
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, chunk)
            if match:
                normalized = _normalize_date(match.group(0))
                if normalized:
                    return normalized, 'high'

    # 无分隔符的 8 位日期，如 20240518
    match = re.search(r'(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)', spaced)
    if match:
        normalized = _build_date(match.group(1), match.group(2), match.group(3))
        if normalized:
            return normalized, 'medium'

    return '', 'low'


def parse_express_info(image_path):
    """对外入口：统一记录整体耗时与失败率，供 /api/metrics 查看 P50/P95。"""
    started = time.perf_counter()
    success = False
    try:
        result = _recognize(image_path)
        success = True
        return result
    finally:
        METRICS.record('recognize', (time.perf_counter() - started) * 1000, success)


def _recognize(image_path):
    client = get_ocr_client()

    preprocess_started = time.perf_counter()
    try:
        image_bytes, quality = preprocess_image(image_path)
    except Exception as exc:
        print(f'[ocr_engine] 图像预处理失败，回退原图: {exc}')
        with open(image_path, 'rb') as fp:
            image_bytes = fp.read()
        quality = None
    METRICS.record('preprocess', (time.perf_counter() - preprocess_started) * 1000)

    ocr_started = time.perf_counter()
    ocr_ok = False
    try:
        result = _run_ocr(client, image_bytes)
        ocr_ok = True
    finally:
        METRICS.record('ocr', (time.perf_counter() - ocr_started) * 1000, ocr_ok)

    parse_started = time.perf_counter()
    lines = _extract_ocr_lines(result)
    full_text = '\n'.join(line['words'] for line in lines)

    image_width = quality.width if quality else 0
    sender_lines, receiver_lines, regions_split = _split_regions(lines, image_width)

    if regions_split:
        sender_text = '\n'.join(line['words'] for line in sender_lines)
        receiver_text = '\n'.join(line['words'] for line in receiver_lines)
        sender_name, sender_name_conf = _extract_person_name(sender_text, SENDER_NAME_KEYWORDS)
        receiver_name, receiver_name_conf = _extract_person_name(receiver_text, RECEIVER_NAME_KEYWORDS)
        sender_phone, sender_phone_conf = _extract_phone(sender_text)
        receiver_phone, receiver_phone_conf = _extract_phone(receiver_text)
        sender_address, sender_addr_conf = _extract_address(sender_text, SENDER_ADDR_KEYWORDS)
        receiver_address, receiver_addr_conf = _extract_address(receiver_text, RECEIVER_ADDR_KEYWORDS)
    else:
        # 无法区分寄收区域时，按出现顺序分配，避免寄收双方取到同一条记录
        persons = _extract_ordered_persons(full_text)
        addresses = _extract_ordered_addresses(full_text)
        sender_name, sender_phone = persons[0] if persons else ('', '')
        receiver_name, receiver_phone = persons[1] if len(persons) > 1 else ('', '')
        sender_address = addresses[0] if addresses else ''
        receiver_address = addresses[1] if len(addresses) > 1 else ''
        sender_name_conf = _confidence(sender_name)
        sender_phone_conf = _confidence(sender_phone)
        sender_addr_conf = _confidence(sender_address)
        receiver_name_conf = _confidence(receiver_name)
        receiver_phone_conf = _confidence(receiver_phone)
        receiver_addr_conf = _confidence(receiver_address)
    amount, amount_conf = extract_amount(full_text)
    express_date, date_conf = extract_date(full_text)
    express_no, no_conf = _extract_express_no(full_text)
    METRICS.record('parse', (time.perf_counter() - parse_started) * 1000)

    return {
        'sender_name': sender_name,
        'sender_phone': sender_phone,
        'sender_address': sender_address,
        'receiver_name': receiver_name,
        'receiver_phone': receiver_phone,
        'receiver_address': receiver_address,
        'amount': amount,
        'express_date': express_date,
        'express_no': express_no,
        'full_text': full_text,
        'image_path': image_path,
        'field_confidence': {
            'sender_name': sender_name_conf,
            'sender_phone': sender_phone_conf,
            'sender_address': sender_addr_conf,
            'receiver_name': receiver_name_conf,
            'receiver_phone': receiver_phone_conf,
            'receiver_address': receiver_addr_conf,
            'amount': amount_conf,
            'express_date': date_conf,
            'express_no': no_conf,
        },
        'image_quality': quality.as_dict() if quality else {},
    }
