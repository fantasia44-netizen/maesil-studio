"""슬롯 기반 썸네일 합성 엔진 (Thumbnail Studio).

배경 → 캐릭터 → 아이콘 → 뱃지/헤드라인/서브 → CTA 바를 '슬롯'에 배치하는
공통 합성 엔진. 하나의 엔진으로 두 티어를 지원한다.

  · 브랜드용 : 브랜드 마스코트/아이콘 PNG(투명 배경)를 슬롯에 합성 → 배마마급 일관 디자인
  · 일반용   : 내장 플레이스홀더 팩(아래 _mascot/_icon)을 사용

레이아웃은 '수직 존 분리' 원칙을 따른다. 텍스트 존(상단)과 캐릭터/아이콘 존(하단)을
겹치지 않게 나눠 슬롯 충돌을 원천 차단하고, 헤드라인은 텍스트 존 크기에 맞춰 자동 축소한다.

텍스트 렌더(검은고딕 + 굵은 외곽선 + 소프트 섀도우)는 imagen_service 헬퍼를 재사용한다.
"""
from __future__ import annotations
import os
import math
import logging
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from services.imagen_service import (
    _find_display_font, _find_korean_font, _draw_line, _hex_to_rgb,
)

logger = logging.getLogger(__name__)

W = H = 1080
MARGIN = 70


# ════════════════════════════════════════════════════════════
# 테마 (색 팔레트) — 브랜드/일반 공통, 이름으로 선택
# ════════════════════════════════════════════════════════════
THEMES = {
    'baby_blue': dict(bg_top='#EAF4FB', bg_bot='#CFE6F5',
                      headline='#2C5B7C', sub='#3D6E8E',
                      badge='#4AA6E0', cta='#4AA6E0'),
    'food_cream': dict(bg_top='#FBF3E7', bg_bot='#F3E2C7',
                       headline='#8A4B1E', sub='#9A6A3A',
                       badge='#E39B3C', cta='#E39B3C'),
    'fresh_green': dict(bg_top='#EEF7EA', bg_bot='#D6EBCB',
                        headline='#3B6B2E', sub='#4E7C40',
                        badge='#6FB94E', cta='#6FB94E'),
    'warm_pink': dict(bg_top='#FDEEF1', bg_bot='#F7D3DC',
                      headline='#9B3B55', sub='#B15A72',
                      badge='#E86A88', cta='#E86A88'),
}

# ════════════════════════════════════════════════════════════
# 템플릿 (레이아웃) — 존 좌표로 슬롯 충돌 원천 차단
#   text_zone   : (x, y, w, h)  헤드라인/서브/뱃지가 들어가는 상단 영역
#   char_slot   : (x, y, w, h)  캐릭터 배치 박스 (하단, 바닥 정렬)
#   icon_slots  : [(cx, cy, size), ...]  장식 아이콘 (텍스트 존과 겹치지 않게)
# ════════════════════════════════════════════════════════════
TEMPLATES = {
    'char_right': dict(
        text_zone=(MARGIN, 96, W - MARGIN * 2, 470),
        char_slot=(560, 566, 470, 470),
        icon_slots=[(175, 700, 150), (215, 880, 128)],
    ),
    'char_left': dict(
        text_zone=(MARGIN, 96, W - MARGIN * 2, 470),
        char_slot=(50, 566, 470, 470),
        icon_slots=[(905, 700, 150), (865, 880, 128)],
    ),
    'char_center': dict(
        text_zone=(MARGIN, 90, W - MARGIN * 2, 430),
        char_slot=(330, 540, 420, 480),
        icon_slots=[(160, 640, 138), (920, 660, 138)],
    ),
}


# ════════════════════════════════════════════════════════════
# 배경 슬롯
# ════════════════════════════════════════════════════════════
def _bg(theme: dict) -> Image.Image:
    img = Image.new('RGBA', (W, H))
    d = ImageDraw.Draw(img)
    tr, br = _hex_to_rgb(theme['bg_top']), _hex_to_rgb(theme['bg_bot'])
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(
            int(tr[0] * (1 - t) + br[0] * t),
            int(tr[1] * (1 - t) + br[1] * t),
            int(tr[2] * (1 - t) + br[2] * t), 255))
    deco = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    for (cx, cy, r, a) in [(150, 800, 120, 24), (940, 250, 92, 28), (900, 910, 66, 20)]:
        dd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, a))
    return Image.alpha_composite(img, deco)


# ════════════════════════════════════════════════════════════
# 캐릭터 슬롯 — 내장 플레이스홀더(파란 목도리 곰). 브랜드 에셋이 있으면 대체.
# ════════════════════════════════════════════════════════════
def _mascot(size: int = 460) -> Image.Image:
    s = size
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    white, line = (250, 250, 252, 255), (120, 130, 150, 255)
    scarf, scarf_d = (90, 175, 225, 255), (70, 150, 200, 255)
    pink = (255, 180, 190, 255)

    def circle(cx, cy, r, fill, outline=None, w=0):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=outline, width=w)

    cx = s // 2
    circle(cx, int(s * 0.72), int(s * 0.26), white, line, 4)          # 몸통
    d.rounded_rectangle([cx - int(s * 0.26), int(s * 0.55),
                         cx + int(s * 0.26), int(s * 0.63)], radius=26, fill=scarf)
    d.rounded_rectangle([cx + int(s * 0.05), int(s * 0.60),
                         cx + int(s * 0.15), int(s * 0.80)], radius=16, fill=scarf_d)
    circle(int(s * 0.34), int(s * 0.20), int(s * 0.09), white, line, 4)   # 귀
    circle(int(s * 0.66), int(s * 0.20), int(s * 0.09), white, line, 4)
    circle(int(s * 0.34), int(s * 0.20), int(s * 0.045), pink)
    circle(int(s * 0.66), int(s * 0.20), int(s * 0.045), pink)
    circle(cx, int(s * 0.36), int(s * 0.24), white, line, 4)              # 머리
    circle(int(s * 0.36), int(s * 0.42), int(s * 0.045), pink)            # 볼
    circle(int(s * 0.64), int(s * 0.42), int(s * 0.045), pink)
    circle(int(s * 0.42), int(s * 0.34), int(s * 0.028), (40, 45, 55, 255))  # 눈
    circle(int(s * 0.58), int(s * 0.34), int(s * 0.028), (40, 45, 55, 255))
    d.ellipse([cx - int(s * 0.03), int(s * 0.40),
               cx + int(s * 0.03), int(s * 0.45)], fill=(90, 95, 110, 255))  # 코
    return im


# ════════════════════════════════════════════════════════════
# 아이콘 슬롯 — 흰 원형 뱃지 + 플랫 아이콘
# ════════════════════════════════════════════════════════════
def _icon(kind: str, size: int = 130) -> Image.Image:
    s = size
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = s // 2
    if kind == 'sun':
        for i in range(12):
            a = math.radians(i * 30)
            d.line([(c + int(math.cos(a) * s * 0.36), c + int(math.sin(a) * s * 0.36)),
                    (c + int(math.cos(a) * s * 0.47), c + int(math.sin(a) * s * 0.47))],
                   fill=(255, 200, 60, 255), width=10)
        d.ellipse([c - s * 0.28, c - s * 0.28, c + s * 0.28, c + s * 0.28],
                  fill=(255, 210, 80, 255))
    elif kind == 'leaf':
        d.ellipse([s * 0.16, s * 0.10, s * 0.84, s * 0.78], fill=(120, 200, 120, 255))
        d.line([(c, s * 0.78), (c, s * 0.30)], fill=(80, 150, 80, 255), width=8)
    elif kind == 'drop':
        d.ellipse([s * 0.22, s * 0.35, s * 0.78, s * 0.86], fill=(90, 180, 230, 255))
        d.polygon([(c, s * 0.10), (s * 0.30, s * 0.55), (s * 0.70, s * 0.55)],
                  fill=(90, 180, 230, 255))
    elif kind == 'heart':
        d.ellipse([s * 0.18, s * 0.22, s * 0.52, s * 0.56], fill=(240, 110, 130, 255))
        d.ellipse([s * 0.48, s * 0.22, s * 0.82, s * 0.56], fill=(240, 110, 130, 255))
        d.polygon([(s * 0.20, s * 0.44), (s * 0.80, s * 0.44), (c, s * 0.82)],
                  fill=(240, 110, 130, 255))
    badge = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(badge).ellipse([2, 2, s - 2, s - 2], fill=(255, 255, 255, 235))
    badge.alpha_composite(im)
    return badge


# ════════════════════════════════════════════════════════════
# 유틸 — 소프트 그림자 / 에셋 로드 / 텍스트 자동맞춤
# ════════════════════════════════════════════════════════════
def _soft_shadow(layer: Image.Image, blur=16, alpha=95) -> Image.Image:
    a = layer.split()[3].point(lambda p: min(alpha, p))
    black = Image.new('RGBA', layer.size, (30, 40, 60, 0))
    black.putalpha(a)
    return black.filter(ImageFilter.GaussianBlur(blur))


def _load_asset(src) -> Image.Image | None:
    """브랜드 에셋 로드 — PIL.Image / 파일경로 / bytes 모두 허용."""
    if src is None:
        return None
    try:
        if isinstance(src, Image.Image):
            return src.convert('RGBA')
        if isinstance(src, (bytes, bytearray)):
            return Image.open(BytesIO(src)).convert('RGBA')
        if isinstance(src, str) and os.path.exists(src):
            return Image.open(src).convert('RGBA')
    except Exception as e:
        logger.warning('[thumb_studio] 에셋 로드 실패 → 기본 캐릭터 사용: %s', e)
    return None


def _fit_in(box_img: Image.Image, w: int, h: int) -> Image.Image:
    """비율 유지하며 (w,h) 안에 맞춤."""
    r = min(w / box_img.width, h / box_img.height)
    return box_img.resize((max(1, int(box_img.width * r)),
                           max(1, int(box_img.height * r))), Image.LANCZOS)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int, draw) -> list[str]:
    """공백 우선 줄바꿈, 없으면 문자 단위."""
    def measure(s):
        b = draw.textbbox((0, 0), s, font=font)
        return b[2] - b[0]
    if measure(text) <= max_w:
        return [text]
    lines, cur = [], ''
    tokens = text.split(' ')
    if len(tokens) > 1:
        for tok in tokens:
            cand = (cur + ' ' + tok).strip()
            if measure(cand) > max_w and cur:
                lines.append(cur); cur = tok
            else:
                cur = cand
        if cur:
            lines.append(cur)
        return lines
    for ch in text:
        if measure(cur + ch) > max_w and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _fit_headline(text, font_path, box_w, box_h, draw,
                  max_size, min_size=48, max_lines=2, line_gap=1.22):
    """텍스트 존(box_w×box_h)에 맞도록 폰트 크기를 자동 축소하며 줄바꿈."""
    size = max_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        lines = _wrap(text, font, box_w, draw)
        if len(lines) <= max_lines:
            asc = font.getbbox('가')[3] - font.getbbox('가')[1]
            total_h = int(asc * line_gap) * len(lines)
            if total_h <= box_h:
                return lines, font, int(asc * line_gap)
        size -= 6
    font = ImageFont.truetype(font_path, min_size)
    lines = _wrap(text, font, box_w, draw)[:max_lines]
    asc = font.getbbox('가')[3] - font.getbbox('가')[1]
    return lines, font, int(asc * line_gap)


def _draw_center_lines(img, lines, font, cx, y, dy, color, stroke):
    """중앙정렬 텍스트 — 소프트 섀도우 + 굵은 외곽선 2패스."""
    d = ImageDraw.Draw(img)
    sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for i, ln in enumerate(lines):
        bb = d.textbbox((0, 0), ln, font=font, stroke_width=stroke)
        sd.text((cx - (bb[2] - bb[0]) // 2 + 4, y + i * dy + 6),
                ln, font=font, fill=(0, 0, 0, 150))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(max(5, round(stroke * 0.8)))))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        bb = d.textbbox((0, 0), ln, font=font, stroke_width=stroke)
        x = cx - (bb[2] - bb[0]) // 2
        _draw_line(d, x, y + i * dy, ln, font, (*_hex_to_rgb(color), 255),
                   stroke, (0, 0, 0, 255))


# ════════════════════════════════════════════════════════════
# 메인 — 슬롯 조립
# ════════════════════════════════════════════════════════════
def render_thumbnail(
    headline: str,
    sub: str = '',
    badge: str = '',
    cta: str = '',
    theme: str = 'baby_blue',
    template: str = 'char_right',
    mascot_src=None,          # 브랜드 마스코트(경로/PIL/bytes). None이면 내장 곰.
    icon_kinds=None,          # 장식 아이콘 종류 리스트. None이면 테마 기본.
    brand_name: str = '',
) -> bytes:
    """썸네일 1080×1080 PNG(bytes) 생성."""
    th = THEMES.get(theme, THEMES['baby_blue'])
    tpl = TEMPLATES.get(template, TEMPLATES['char_right'])
    img = _bg(th)
    d = ImageDraw.Draw(img)

    tz_x, tz_y, tz_w, tz_h = tpl['text_zone']
    cx = tz_x + tz_w // 2

    # ── 캐릭터 슬롯 (그림자 포함, 바닥 정렬) ──────────────────
    char = _load_asset(mascot_src) or _mascot(tpl['char_slot'][2])
    cs_x, cs_y, cs_w, cs_h = tpl['char_slot']
    char = _fit_in(char, cs_w, cs_h)
    px = cs_x + (cs_w - char.width) // 2
    py = cs_y + (cs_h - char.height)        # 바닥 정렬
    img.alpha_composite(_soft_shadow(char), dest=(px + 8, py + 14))
    img.alpha_composite(char, dest=(px, py))

    # ── 아이콘 슬롯 ─────────────────────────────────────────
    kinds = icon_kinds or ['sun', 'drop']
    for (icx, icy, isz), kind in zip(tpl['icon_slots'], kinds):
        g = _icon(kind, isz)
        img.alpha_composite(g, dest=(icx - isz // 2, icy - isz // 2))

    # ── 텍스트 존: 뱃지 + 헤드라인 + 서브 (수직 중앙 팩) ──────
    d = ImageDraw.Draw(img)
    badge_h = 0
    if badge:
        bf = ImageFont.truetype(_find_korean_font(bold=True), 44)
        bb = d.textbbox((0, 0), badge, font=bf)
        badge_h = (bb[3] - bb[1]) + 44 + 26  # pill 높이 + 아래 간격

    # 헤드라인 자동 맞춤 (뱃지/서브 높이만큼 텍스트 존에서 차감)
    sub_reserve = 92 if sub else 0
    hl_box_h = tz_h - badge_h - sub_reserve
    fp = _find_display_font()
    hl_lines, hl_font, hl_dy = _fit_headline(
        headline, fp, tz_w, hl_box_h, d, max_size=132, max_lines=2)
    hl_h = hl_dy * len(hl_lines)
    stroke = max(3, round(hl_font.size * 0.10))

    block_h = badge_h + hl_h + sub_reserve
    y = tz_y + max(0, (tz_h - block_h) // 2)

    if badge:
        bf = ImageFont.truetype(_find_korean_font(bold=True), 44)
        bb = d.textbbox((0, 0), badge, font=bf)
        tw, tht = bb[2] - bb[0], bb[3] - bb[1]
        px0, px1 = cx - tw // 2 - 40, cx + tw // 2 + 40
        d.rounded_rectangle([px0, y, px1, y + tht + 44],
                            radius=(tht + 44) // 2, fill=(*_hex_to_rgb(th['badge']), 255))
        d.text((cx - tw // 2, y + 22 - bb[1]), badge, font=bf, fill=(255, 255, 255, 255))
        y += badge_h

    _draw_center_lines(img, hl_lines, hl_font, cx, y, hl_dy, th['headline'], stroke)
    y += hl_h

    if sub:
        y += 10
        # 서브는 한 줄에 다 들어가도록 폭에 맞춰 자동 축소 (잘림 방지)
        dd = ImageDraw.Draw(img)
        sfp = _find_korean_font(bold=True)
        s_size = 58
        while s_size >= 38:
            sf = ImageFont.truetype(sfp, s_size)
            bb = dd.textbbox((0, 0), sub, font=sf, stroke_width=4)
            if bb[2] - bb[0] <= tz_w:
                break
            s_size -= 3
        sf = ImageFont.truetype(sfp, s_size)
        sub_lines = _wrap(sub, sf, tz_w, dd)[:2]  # 최소크기로도 넘치면 2줄 허용
        _draw_center_lines(img, sub_lines, sf, cx, y,
                           int(s_size * 1.18), th['sub'], stroke=4)

    # ── CTA 바 ─────────────────────────────────────────────
    if cta:
        d = ImageDraw.Draw(img)
        bar = 96
        d.rectangle([0, H - bar, W, H], fill=(*_hex_to_rgb(th['cta']), 255))
        cf = ImageFont.truetype(_find_korean_font(bold=True), 46)
        bb = d.textbbox((0, 0), cta, font=cf)
        d.text(((W - (bb[2] - bb[0])) // 2 + 2, H - bar + 26), cta,
               font=cf, fill=(0, 0, 0, 70))
        d.text(((W - (bb[2] - bb[0])) // 2, H - bar + 24), cta,
               font=cf, fill=(255, 255, 255, 255))

    # ── 워터마크 ────────────────────────────────────────────
    if brand_name and not cta:
        mk = brand_name if brand_name.startswith('@') else f'@{brand_name}'
        mf = ImageFont.truetype(_find_korean_font(bold=False), 40)
        bb = ImageDraw.Draw(img).textbbox((0, 0), mk, font=mf)
        ImageDraw.Draw(img).text(((W - (bb[2] - bb[0])) // 2, H - 86), mk,
                                 font=mf, fill=(120, 120, 130, 200))

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()
