"""快递单图片预处理：方向摆正、倾斜校正、降采样、去噪增强与质量评估。

目标是在送 OCR 之前先治理图像质量，重点解决模糊、倾斜、光照不均、
复杂背景四类场景。OpenCV 不可用时自动降级为纯 Pillow 实现，保证识别流程不中断。
"""
import io
import math

from PIL import Image, ImageFilter, ImageOps

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

# 长边超过该值先降采样，兼顾识别精度与耗时（百度 OCR 上限为 4096px）
MAX_SIDE = 1800
# 长边低于该值不做放大
MIN_SIDE = 900

BLUR_THRESHOLD = 80.0        # Laplacian 方差低于该值视为模糊（经验值，仅作标记）
LOW_CONTRAST_STD = 45.0      # 灰度标准差低于该值视为对比度不足

SKEW_MIN_ANGLE = 0.4         # 小于该角度不做旋转，避免无意义重采样
SKEW_MAX_ANGLE = 15.0        # 超过该角度认为不是倾斜，不做旋转

_JPEG_QUALITY = 92
_MAX_OUTPUT_BYTES = 3.8 * 1024 * 1024


class ImageQuality:
    """预处理结果的质量指标，供上层做提示与埋点。"""

    def __init__(self):
        self.width = 0
        self.height = 0
        self.blur_score = -1.0
        self.is_blurry = False
        self.low_contrast = False
        self.skew_angle = 0.0
        self.steps = []
        self.cv2_available = cv2 is not None

    def as_dict(self):
        return {
            'width': self.width,
            'height': self.height,
            'blur_score': round(self.blur_score, 1),
            'is_blurry': self.is_blurry,
            'low_contrast': self.low_contrast,
            'skew_angle': round(self.skew_angle, 2),
            'steps': list(self.steps),
        }


def preprocess_image(image_path):
    """读取图片并做质量治理，返回 (处理后图片字节, ImageQuality)。"""
    quality = ImageQuality()

    img = ImageOps.exif_transpose(Image.open(image_path))
    if img.mode != 'RGB':
        img = img.convert('RGB')
    quality.steps.append('exif')

    before = max(img.size)
    img = _downscale(img)
    if max(img.size) != before:
        quality.steps.append('downscale')

    gray = _to_gray_array(img)
    if gray is not None:
        quality.blur_score = _blur_score(gray)
        quality.is_blurry = 0 < quality.blur_score < BLUR_THRESHOLD
        quality.low_contrast = float(gray.std()) < LOW_CONTRAST_STD

        skew = _estimate_skew(gray)
        quality.skew_angle = skew
        if SKEW_MIN_ANGLE < abs(skew) <= SKEW_MAX_ANGLE:
            img = _rotate(img, skew)
            quality.steps.append('deskew')

    img = _denoise(img)
    quality.steps.append('denoise')

    if cv2 is not None:
        img = _apply_clahe(img)
        quality.steps.append('clahe')

    if quality.low_contrast:
        img = _binarize(img)
        quality.steps.append('binarize')

    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=3))
    quality.steps.append('sharpen')

    quality.width, quality.height = img.size
    return _encode(img), quality


def _downscale(img, max_side=MAX_SIDE):
    width, height = img.size
    long_side = max(width, height)
    if long_side <= max_side:
        return img
    scale = max_side / float(long_side)
    return img.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)


def _to_gray_array(img):
    if np is None:
        return None
    return np.asarray(img.convert('L'), dtype='uint8')


def _blur_score(gray):
    """Laplacian 方差，值越小越模糊。"""
    if cv2 is not None:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if np is not None:
        edges = np.asarray(Image.fromarray(gray).filter(ImageFilter.FIND_EDGES), dtype='float32')
        return float(edges.var())
    return -1.0


def _estimate_skew(gray):
    """用霍夫直线估计主方向倾角，返回需要旋转的角度（度）。"""
    if cv2 is None:
        return 0.0
    try:
        edges = cv2.Canny(gray, 50, 150)
        min_len = max(60, gray.shape[1] // 6)
        lines = cv2.HoughLinesP(edges, 1, math.pi / 180, threshold=80,
                                minLineLength=min_len, maxLineGap=12)
        if lines is None:
            return 0.0
        angles = []
        for x1, y1, x2, y2 in lines[:, 0]:
            angle = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
            while angle <= -90:
                angle += 180
            while angle > 90:
                angle -= 180
            if abs(angle) <= SKEW_MAX_ANGLE:
                angles.append(angle)
        if len(angles) < 5:
            return 0.0
        return float(np.median(angles))
    except Exception:
        return 0.0


def _rotate(img, angle):
    return img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))


def _denoise(img):
    if cv2 is None:
        return img.filter(ImageFilter.MedianFilter(size=3))
    try:
        # NLM 的耗时与搜索窗面积成正比。原 searchWindowSize=21 时单张要 1.7s（占整个
        # 预处理的 90%），收紧到 7 后约快 9 倍；对这类轻度噪声的抑制效果基本不变。
        arr = cv2.fastNlMeansDenoisingColored(np.array(img), None, 5, 5, 7, 7)
        return Image.fromarray(arr)
    except Exception:
        return img.filter(ImageFilter.MedianFilter(size=3))


def _apply_clahe(img):
    """限制对比度自适应直方图均衡，改善光照不均与低对比。"""
    try:
        lab = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        return Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
    except Exception:
        return img


def _binarize(img):
    """自适应二值化，用于背景复杂的印章/彩色运单。"""
    try:
        if cv2 is None:
            raise RuntimeError('no cv2')
        gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, 31, 12)
        return Image.fromarray(binary).convert('RGB')
    except Exception:
        return img.convert('L').point(lambda p: 255 if p > 140 else 0).convert('RGB')


def _encode(img):
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=_JPEG_QUALITY, optimize=True)
    if buffer.tell() > _MAX_OUTPUT_BYTES:
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=70, optimize=True)
    return buffer.getvalue()
