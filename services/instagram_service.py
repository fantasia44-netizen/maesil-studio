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
    candidates = [
        'C:/Windows/Fonts/malgunbd.ttf' if bold else 'C:/Windows/Fonts/malgun.ttf',
        '/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf' if bold
            else '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
        '/System/Library/Fonts/AppleSDGothicNeo.ttc',
        '/usr/share/fonts/noto/NotoSansCJK-Bold.ttc' if bold
            else '/usr/share/fonts/noto/NotoSansCJK-Regular.ttc',
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    raise FileNotFoundError('한국어 폰트를 찾을 수 없습니다.')


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
                        pil_size: tuple = (1080, 1080)) -> str:
    """FLUX 배경 + 하단 그라디언트 배너 + 한글 텍스트 레이어"""
    img = _load(bg_url).resize(pil_size, Image.LANCZOS)
    W, H = img.size

    # ── 그라디언트 오버레이 ──────────────────────────────
    ov  = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    dov = ImageDraw.Draw(ov)

    gs = int(H * 0.55)
    for y in range(gs, H):
        a = int(220 * (y - gs) / (H - gs))
        dov.line([(0, y), (W, y)], fill=(8, 8, 8, a))

    # 브랜드 컬러 바 (맨 아래 12px)
    br, bg_, bb = _hex_rgb(brand_color)
    dov.rectangle([(0, H - 12), (W, H)], fill=(br, bg_, bb, 255))

    combined = Image.alpha_composite(img, ov)
    d = ImageDraw.Draw(combined)

    # ── 폰트 ────────────────────────────────────────────
    try:
        fp = _font(bold=True)
        f1 = ImageFont.truetype(fp, int(H * 0.076))
        f2 = ImageFont.truetype(fp, int(H * 0.045))
        f3 = ImageFont.truetype(fp, int(H * 0.034))
    except Exception:
        f1 = f2 = f3 = ImageFont.load_default()

    fonts = [f1, f2, f3]
    y = int(H * 0.59)

    for i, text in enumerate(texts[:3]):
        if not text:
            continue
        font  = fonts[min(i, 2)]
        lines = _wrap(text, font, int(W * 0.87))[:2]
        for ln in lines:
            # 드롭 섀도
            d.text((int(W * 0.06) + 2, y + 2), ln, font=font, fill=(0, 0, 0, 160))
            d.text((int(W * 0.06),     y    ), ln, font=font, fill=(255, 255, 255, 255))
            bb = font.getbbox(ln)
            y += int((bb[3] - bb[1]) * 1.35)
        y += int(H * 0.008)

    return _jpeg_b64(combined)


# ════════════════════════════════════════════════════════
# Style 2 — 웹툰 말풍선
# ════════════════════════════════════════════════════════

def create_webtoon_image(bg_url: str,
                         dialogues: list[str],
                         pil_size: tuple = (1080, 1080)) -> str:
    """웹툰 스타일 배경 + 한글 말풍선 (최대 2개)"""
    img = _load(bg_url).resize(pil_size, Image.LANCZOS)
    W, H = img.size
    d   = ImageDraw.Draw(img)

    try:
        fp   = _font(bold=True)
        font = ImageFont.truetype(fp, int(H * 0.050))
    except Exception:
        font = ImageFont.load_default()

    # 말풍선 기본 위치 (좌상 / 우중)
    cfgs = [
        {'ax': int(W * 0.06), 'ay': int(H * 0.05),  'tail': 'down'},
        {'ax': int(W * 0.30), 'ay': int(H * 0.50),  'tail': 'up'},
    ]

    for dlg, cfg in zip(dialogues[:2], cfgs):
        if dlg.strip():
            _bubble(d, dlg, cfg, font, W, H)

    return _jpeg_b64(img)


def _bubble(draw: ImageDraw.ImageDraw, text: str, cfg: dict,
            font: ImageFont.ImageFont, W: int, H: int):
    PAD, TAIL, R = 26, 33, 22
    max_w = int(W * 0.52)

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
