"""인스타그램 이미지 합성 서비스 — 스타일별 PIL 컴포지팅

스타일:
  realistic_banner  — FLUX Schnell + PIL 하단 그라디언트 배너 + 한글 텍스트
  webtoon           — FLUX Schnell 웹툰 씬 + PIL 말풍선 + 한글 대사
  typography        — Ideogram 3.0 (한글 네이티브) — PIL 불필요
"""
import base64
import logging
import os
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

SIZE_MAP = {
    '1:1':  ('1080x1080', (1080, 1080)),
    '4:5':  ('1080x1350', (1080, 1350)),
    '9:16': ('1080x1920', (1080, 1920)),
}


# ── 내부 유틸 ──────────────────────────────────────────────────

def _font(bold: bool = False) -> str:
    _b = bold
    # 프로젝트 내 번들 폰트 (render.yaml buildCommand 에서 다운로드)
    _here = os.path.dirname(os.path.abspath(__file__))
    _proj_root = os.path.join(_here, '..')
    proj_fonts = [
        os.path.join(_proj_root, 'static', 'fonts', 'NanumGothicBold.ttf' if _b else 'NanumGothic.ttf'),
    ]

    candidates = proj_fonts + [
        # Windows
        'C:/Windows/Fonts/malgunbd.ttf' if _b else 'C:/Windows/Fonts/malgun.ttf',
        'C:/Windows/Fonts/NanumGothicBold.ttf' if _b else 'C:/Windows/Fonts/NanumGothic.ttf',
        # Linux / Render (Nanum - apt-get install fonts-nanum)
        '/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf' if _b
            else '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
        '/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf' if _b
            else '/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf',
        # Linux / Render (Noto CJK - apt-get install fonts-noto-cjk)
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc' if _b
            else '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/noto-cjk/NotoSansCJKkr-Bold.otf' if _b
            else '/usr/share/fonts/noto-cjk/NotoSansCJKkr-Regular.otf',
        '/usr/share/fonts/noto/NotoSansCJK-Bold.ttc' if _b
            else '/usr/share/fonts/noto/NotoSansCJK-Regular.ttc',
        # macOS
        '/System/Library/Fonts/AppleSDGothicNeo.ttc',
        '/Library/Fonts/NanumGothic.ttf',
    ]

    for p in candidates:
        if os.path.exists(p):
            return p

    # 최후 폴백: /tmp/ 에 런타임 다운로드
    return _download_fallback_font(_b)


def _download_fallback_font(bold: bool = False) -> str:
    """한글 폰트가 없을 때 /tmp/ 에 다운로드 (최초 1회만)."""
    import urllib.request
    tmp_dir = '/tmp/maesil_fonts'
    os.makedirs(tmp_dir, exist_ok=True)
    fname = 'NanumGothicBold.ttf' if bold else 'NanumGothic.ttf'
    path  = os.path.join(tmp_dir, fname)
    if os.path.exists(path):
        return path
    urls = {
        'NanumGothic.ttf':     'https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf',
        'NanumGothicBold.ttf': 'https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Bold.ttf',
    }
    try:
        logger.info('[font] 폰트 다운로드 중: %s', fname)
        urllib.request.urlretrieve(urls[fname], path)
        logger.info('[font] 폰트 다운로드 완료: %s', path)
        return path
    except Exception as e:
        raise FileNotFoundError(f'한글 폰트를 찾을 수 없고 다운로드도 실패했습니다: {e}')


def _hex_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _load(url_or_data: str) -> Image.Image:
    if url_or_data.startswith('data:image/'):
        _, b64 = url_or_data.split(',', 1)
        return Image.open(BytesIO(base64.b64decode(b64))).convert('RGBA')
    r = requests.get(url_or_data, timeout=35)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert('RGBA')


def _jpeg_b64(img: Image.Image, quality: int = 93) -> str:
    buf = BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=quality)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def _wrap(text: str, font: ImageFont.ImageFont, max_px: int) -> list[str]:
    """픽셀 너비 기준 줄바꿈 (한글 한 글자씩)"""
    lines, cur = [], ''
    for ch in text:
        test = cur + ch
        bb = font.getbbox(test)
        if (bb[2] - bb[0]) > max_px and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


# ════════════════════════════════════════════════════════
# Style 1 — 실사 배너
# ════════════════════════════════════════════════════════

def create_banner_image(bg_url: str,
                        texts: list[str],
                        brand_color: str = '#e8355a',
                        pil_size: tuple = (1080, 1080),
                        text_gravity: str = 'bottom-left',
                        text_scale: float = 1.0) -> str:
    """FLUX 배경 + 그라디언트 배너 + 한글 텍스트 레이어

    text_gravity: 'bottom-left'(기본), 'bottom-center', 'top-left', 'top-center', 'center-left'
    text_scale:   폰트 사이즈 배율 (기본 1.0)
    """
    img = _load(bg_url).resize(pil_size, Image.LANCZOS)
    W, H = img.size

    # ── 위치별 파라미터 결정 ─────────────────────────────
    is_top    = text_gravity.startswith('top')
    is_center = text_gravity.startswith('center')

    if is_top:
        gs_top_ratio, gs_bot_ratio = 0.0,  0.35
        y_start,      x_start      = 0.06, 0.06 if 'left' in text_gravity else 0.25
    elif is_center:
        gs_top_ratio, gs_bot_ratio = 0.25, 0.70
        y_start,      x_start      = 0.38, 0.06
    else:  # bottom-left / bottom-center
        gs_top_ratio, gs_bot_ratio = 0.55, 1.0
        y_start,      x_start      = 0.59, 0.06 if 'left' in text_gravity else 0.25

    # ── 그라디언트 오버레이 ──────────────────────────────
    ov  = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    dov = ImageDraw.Draw(ov)

    if is_top:
        # 위에서 아래로 진하게 → 아래는 투명
        top_end = int(H * gs_bot_ratio)
        for y_px in range(0, top_end):
            a = int(220 * (1 - y_px / top_end))
            dov.line([(0, y_px), (W, y_px)], fill=(8, 8, 8, a))
    else:
        # 기존 방식 (bottom / center)
        gs = int(H * gs_top_ratio)
        gs_end = int(H * gs_bot_ratio) if not is_center else H
        for y_px in range(gs, gs_end):
            a = int(220 * (y_px - gs) / (gs_end - gs))
            dov.line([(0, y_px), (W, y_px)], fill=(8, 8, 8, a))

    # 브랜드 컬러 바 (맨 아래 12px)
    br, bg_, bb = _hex_rgb(brand_color)
    dov.rectangle([(0, H - 12), (W, H)], fill=(br, bg_, bb, 255))

    combined = Image.alpha_composite(img, ov)
    d = ImageDraw.Draw(combined)

    # ── 폰트 ────────────────────────────────────────────
    try:
        fp = _font(bold=True)
        f1 = ImageFont.truetype(fp, int(H * 0.076 * text_scale))
        f2 = ImageFont.truetype(fp, int(H * 0.045 * text_scale))
        f3 = ImageFont.truetype(fp, int(H * 0.034 * text_scale))
    except Exception:
        f1 = f2 = f3 = ImageFont.load_default()

    fonts = [f1, f2, f3]
    y = int(H * y_start)
    x = int(W * x_start)

    for i, text in enumerate(texts[:3]):
        if not text:
            continue
        font  = fonts[min(i, 2)]
        lines = _wrap(text, font, int(W * 0.87))[:2]
        for ln in lines:
            # 드롭 섀도
            d.text((x + 2, y + 2), ln, font=font, fill=(0, 0, 0, 160))
            d.text((x,     y    ), ln, font=font, fill=(255, 255, 255, 255))
            bb = font.getbbox(ln)
            y += int((bb[3] - bb[1]) * 1.35)
        y += int(H * 0.008)

    return _jpeg_b64(combined)


# ════════════════════════════════════════════════════════
# Style 2 — 웹툰 말풍선
# ════════════════════════════════════════════════════════

def create_webtoon_image(bg_url: str,
                         dialogues: list[str],
                         pil_size: tuple = (1080, 1080),
                         bubble_layout: str = 'default') -> str:
    """웹툰 스타일 배경 + 한글 말풍선 (최대 2개)

    bubble_layout: 'default', 'top-right', 'bottom-both', 'top-both'
    """
    img = _load(bg_url).resize(pil_size, Image.LANCZOS)
    W, H = img.size
    d   = ImageDraw.Draw(img)

    try:
        fp   = _font(bold=True)
        font = ImageFont.truetype(fp, int(H * 0.038))  # 54→41px: 버블 크기 축소
    except Exception:
        font = ImageFont.load_default()

    # 말풍선 레이아웃 — 캐릭터가 주로 중하단에 위치하므로 버블은 상단 배치
    LAYOUTS = {
        'default':     [
            {'ax': int(W * 0.04), 'ay': int(H * 0.04),  'tail': 'down'},  # 상단 좌
            {'ax': int(W * 0.53), 'ay': int(H * 0.04),  'tail': 'down'},  # 상단 우
        ],
        'top-right':   [
            {'ax': int(W * 0.53), 'ay': int(H * 0.04),  'tail': 'down'},  # 상단 우
            {'ax': int(W * 0.04), 'ay': int(H * 0.22),  'tail': 'down'},  # 상단 좌 아래
        ],
        'bottom-both': [
            {'ax': int(W * 0.04), 'ay': int(H * 0.73),  'tail': 'up'},    # 하단 좌
            {'ax': int(W * 0.53), 'ay': int(H * 0.73),  'tail': 'up'},    # 하단 우
        ],
        'top-both':    [
            {'ax': int(W * 0.04), 'ay': int(H * 0.04),  'tail': 'down'},  # 상단 좌 위
            {'ax': int(W * 0.04), 'ay': int(H * 0.23),  'tail': 'down'},  # 상단 좌 아래
        ],
    }
    cfgs = LAYOUTS.get(bubble_layout, LAYOUTS['default'])

    for dlg, cfg in zip(dialogues[:2], cfgs):
        if dlg.strip():
            _bubble(d, dlg, cfg, font, W, H)

    return _jpeg_b64(img)


def _bubble(draw: ImageDraw.ImageDraw, text: str, cfg: dict,
            font: ImageFont.ImageFont, W: int, H: int):
    PAD, TAIL, R = 18, 24, 18
    max_w = int(W * 0.38)  # 0.52→0.38: 버블 너비 축소

    lines = _wrap(text, font, max_w)[:3]
    if not lines:
        return

    lh = int(font.size * 1.44)
    tw = max((font.getbbox(l)[2] - font.getbbox(l)[0]) for l in lines)
    th = len(lines) * lh
    bw, bh = tw + PAD * 2, th + PAD * 2

    ax, ay, tail = cfg['ax'], cfg['ay'], cfg['tail']
    if tail == 'down':
        x1, y1, x2, y2 = ax, ay, ax + bw, ay + bh
    else:
        x1, y1, x2, y2 = ax, ay - bh, ax + bw, ay

    # 화면 경계 보정
    if x2 > W - 20: dx = x2 - (W - 20); x1 -= dx; x2 -= dx
    if x1 < 20:     dx = 20 - x1;        x1 += dx; x2 += dx
    if y2 > H - 55: dy = y2 - (H - 55); y1 -= dy; y2 -= dy
    if y1 < 10:     dy = 10 - y1;        y1 += dy; y2 += dy

    # 테두리 + 흰 배경
    draw.rounded_rectangle([x1 - 4, y1 - 4, x2 + 4, y2 + 4], radius=R + 4, fill='#111111')
    draw.rounded_rectangle([x1,     y1,     x2,     y2    ], radius=R,     fill='white')

    cx = (x1 + x2) // 2
    if tail == 'down':
        ty = y2
        draw.polygon([(cx - 15, ty - 2), (cx + 15, ty - 2), (cx, ty + TAIL)],     fill='#111111')
        draw.polygon([(cx - 12, ty),     (cx + 12, ty),     (cx, ty + TAIL - 6)], fill='white')
    else:
        ty = y1
        draw.polygon([(cx - 15, ty + 2), (cx + 15, ty + 2), (cx, ty - TAIL)],     fill='#111111')
        draw.polygon([(cx - 12, ty),     (cx + 12, ty),     (cx, ty - TAIL + 6)], fill='white')

    ty2 = y1 + PAD
    for ln in lines:
        draw.text((x1 + PAD, ty2), ln, font=font, fill='#111111')
        ty2 += lh
