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
    # AI 씬 배경 위 상단 텍스트 존 (캐릭터·아이콘은 배경에 이미 그려져 있음)
    'scene_top': dict(
        text_zone=(MARGIN, 60, W - MARGIN * 2, 360),
        char_slot=(0, 0, 1, 1),
        icon_slots=[],
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


def _has_transparency(img: Image.Image) -> bool:
    """이미 투명 영역이 있는(=누끼 처리된) 이미지인지 판별."""
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        return img.convert('RGBA').getchannel('A').getextrema()[0] < 245
    return False


def auto_cutout(img: Image.Image, thresh: int = 30, feather: float = 1.2) -> Image.Image:
    """단색 배경(흰/파스텔)을 모서리 flood-fill로 제거해 투명 PNG로 만든다 — 무료.

    · 모서리부터 채워 들어가므로 피사체 내부의 흰색(흰 곰 몸통 등)은 보존된다.
    · 모서리 색이 불균일(단색 배경 아님)하거나 결과가 비정상(거의 다/거의 안 지워짐)이면
      원본을 그대로 반환한다 → 복잡한 사진 배경은 AI 정밀 누끼로 유도.
    """
    rgb = img.convert('RGB')
    w, h = rgb.size
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]

    def _d(a, b):
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))

    base = rgb.getpixel(corners[0])
    if any(_d(base, rgb.getpixel(c)) > thresh * 2 for c in corners[1:]):
        return img.convert('RGBA')            # 모서리 색 불균일 → 단색 배경 아님, 스킵

    MARKER = (0, 254, 1)                       # 이미지에 거의 없는 마커색
    work = rgb.copy()
    seeds = corners + [(w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for sx, sy in seeds:
        if work.getpixel((sx, sy)) != MARKER:
            ImageDraw.floodfill(work, (sx, sy), MARKER, thresh=thresh)

    px = list(work.getdata())
    frac = sum(1 for p in px if p == MARKER) / float(w * h)
    if frac < 0.01 or frac > 0.97:            # 아무것도 안 지워짐 / 피사체까지 먹음 → 원본 유지
        return img.convert('RGBA')

    mask = Image.new('L', (w, h), 255)
    mask.putdata([0 if p == MARKER else 255 for p in px])
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
    out = img.convert('RGBA')
    out.putalpha(mask)
    return out


def fill_alpha_holes(img: Image.Image, fill=(255, 255, 255)) -> Image.Image:
    """알파의 '내부 구멍'(외곽선에 둘러싸여 이미지 밖과 이어지지 않은 투명부)을
    채워 불투명화한다.

    흰 몸통+검은 외곽선인 선화 캐릭터가 AI 매팅(birefnet)으로 몸통까지 뚫린 경우,
    외곽선(불투명)에 둘러싸인 몸통 구멍만 골라 흰색으로 복원한다. 이미지 가장자리와
    이어진 진짜 배경(투명)은 건드리지 않는다.
    """
    img = img.convert('RGBA')
    w, h = img.size
    a = img.getchannel('A')
    trans = a.point(lambda p: 255 if p <= 32 else 0)   # 투명=255, 불투명=0
    work = trans.convert('RGB')                          # 투명→흰, 불투명→검
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if work.getpixel(seed) == (255, 255, 255):
            ImageDraw.floodfill(work, seed, (255, 0, 0), thresh=10)  # 외부 투명 → 빨강
    holes = work.getchannel('G')                         # 내부 구멍만 255 (외부=빨강 G0)
    if holes.getextrema()[1] == 0:
        return img                                        # 구멍 없음
    patch = Image.new('RGBA', (w, h), (*fill, 255))
    return Image.composite(patch, img, holes)


def _load_asset(src, auto_cut: bool = True) -> Image.Image | None:
    """브랜드 에셋 로드 — PIL.Image / 파일경로 / bytes 모두 허용.

    불투명 이미지(JPG 등)면 auto_cut=True일 때 무료 누끼로 배경을 자동 제거한다.
    """
    if src is None:
        return None
    img = None
    try:
        if isinstance(src, Image.Image):
            img = src.convert('RGBA')
        elif isinstance(src, (bytes, bytearray)):
            img = Image.open(BytesIO(src)).convert('RGBA')
        elif isinstance(src, str) and os.path.exists(src):
            img = Image.open(src).convert('RGBA')
    except Exception as e:
        logger.warning('[thumb_studio] 에셋 로드 실패 → 기본 캐릭터 사용: %s', e)
        return None
    if img is None:
        return None
    if auto_cut and not _has_transparency(img):
        try:
            img = auto_cutout(img)
        except Exception as e:
            logger.warning('[thumb_studio] 자동 누끼 실패 → 원본 사용: %s', e)
    return img


def _fit_in(box_img: Image.Image, w: int, h: int) -> Image.Image:
    """비율 유지하며 (w,h) 안에 맞춤."""
    r = min(w / box_img.width, h / box_img.height)
    return box_img.resize((max(1, int(box_img.width * r)),
                           max(1, int(box_img.height * r))), Image.LANCZOS)


def _fit_cover(box_img: Image.Image, w: int, h: int) -> Image.Image:
    """비율 유지하며 (w,h)를 꽉 채우고 중앙 크롭 (배경 이미지용)."""
    r = max(w / box_img.width, h / box_img.height)
    rz = box_img.resize((max(1, int(box_img.width * r)),
                         max(1, int(box_img.height * r))), Image.LANCZOS)
    left = (rz.width - w) // 2
    top = (rz.height - h) // 2
    return rz.crop((left, top, left + w, top + h))


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


def _title_plate(img, cx, top, width, height, radius=44, fill=(255, 255, 255, 248)):
    """씬 상단 타이틀 플레이트 — 라운드 패널 + 소프트 섀도우.

    fill 로 색을 지정(흰 배너=흰색, 컬러 배너=테마색). 거의 불투명하게 깔아
    뒤 배경이 비치지 않게 해 텍스트 대비를 확보한다.
    """
    x0, x1 = int(cx - width // 2), int(cx + width // 2)
    sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [x0, top, x1, top + height], radius=radius, fill=(30, 40, 60, 120))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(22)))
    plate = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle(
        [x0, top, x1, top + height], radius=radius, fill=fill)
    img.alpha_composite(plate)


def _missing(font, ch) -> bool:
    """폰트가 이 글자를 렌더 못 하는지(글리프 없음). 공백은 제외."""
    if not ch.strip():
        return False
    try:
        return font.getmask(ch).getbbox() is None
    except Exception:
        return False


def _glyph_font(ch, font, fb):
    """글리프가 primary 폰트에 없으면 fallback 폰트로."""
    return fb if (fb is not None and _missing(font, ch)) else font


def _line_width(d, ln, font, fb):
    return sum(d.textlength(ch, font=_glyph_font(ch, font, fb)) for ch in ln)


def _draw_center_lines(img, lines, font, cx, y, dy, color, stroke, fallback_path=None):
    """중앙정렬 텍스트 — 소프트 섀도우 + 굵은 외곽선.

    디스플레이 폰트에 없는 글리프(예: 검은고딕의 가운뎃점 '·')는 fallback_path
    폰트로 글자 단위 대체 렌더해 '빈칸'을 막는다. 미지원 글자가 없는 줄은 기존 방식
    (한 번에 렌더)을 그대로 써서 외곽선 모양을 유지한다.
    """
    fb = None
    if fallback_path:
        try:
            fb = ImageFont.truetype(fallback_path, font.size)
        except Exception:
            fb = None

    def needs_fb(ln):
        return fb is not None and any(_missing(font, ch) for ch in ln)

    d = ImageDraw.Draw(img)
    sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for i, ln in enumerate(lines):
        yy = y + i * dy
        if needs_fb(ln):
            x = cx - _line_width(d, ln, font, fb) / 2
            for ch in ln:
                f = _glyph_font(ch, font, fb)
                sd.text((x + 4, yy + 6), ch, font=f, fill=(0, 0, 0, 150))
                x += d.textlength(ch, font=f)
        else:
            bb = d.textbbox((0, 0), ln, font=font, stroke_width=stroke)
            sd.text((cx - (bb[2] - bb[0]) // 2 + 4, yy + 6), ln, font=font, fill=(0, 0, 0, 150))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(max(5, round(stroke * 0.8)))))

    d = ImageDraw.Draw(img)
    fill = (*_hex_to_rgb(color), 255)
    for i, ln in enumerate(lines):
        yy = y + i * dy
        if needs_fb(ln):
            x = cx - _line_width(d, ln, font, fb) / 2
            for ch in ln:
                f = _glyph_font(ch, font, fb)
                d.text((x, yy), ch, font=f, fill=fill,
                       stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
                x += d.textlength(ch, font=f)
        else:
            bb = d.textbbox((0, 0), ln, font=font, stroke_width=stroke)
            _draw_line(d, cx - (bb[2] - bb[0]) // 2, yy, ln, font, fill, stroke, (0, 0, 0, 255))


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
    auto_cut: bool = True,    # 불투명 마스코트 업로드 시 무료 자동 누끼 여부
    bg_image=None,            # AI 씬 배경(경로/PIL/bytes). 있으면 그라데이션·캐릭터·아이콘 스킵.
    title_plate: bool = True, # 씬 모드에서 상단 타이틀 플레이트 표시
    title_style: str = 'banner',  # 씬 제목 스타일 'banner'(컬러+흰글자) | 'plate'(흰+진한글자)
    title_v_frac: float = 0.0, # 씬 제목 박스 수직 위치 0.0=상단(기본) ~ 1.0=하단
) -> bytes:
    """썸네일 1080×1080 PNG(bytes) 생성.

    bg_image가 주어지면 'AI 씬 하이브리드' 모드로 동작 — 배경 일러스트는 그대로 두고
    상단 텍스트 존에만 선명한 PIL 한글 텍스트를 얹는다.
    """
    th = THEMES.get(theme, THEMES['baby_blue'])
    scene = None
    if bg_image is not None:
        scene = _load_asset(bg_image, auto_cut=False)
    if scene is not None:
        template = 'scene_top'                    # 씬 모드는 상단 텍스트 존 고정
        img = _fit_cover(scene, W, H).convert('RGBA')
    else:
        img = _bg(th)
    tpl = TEMPLATES.get(template, TEMPLATES['char_right'])
    d = ImageDraw.Draw(img)

    tz_x, tz_y, tz_w, tz_h = tpl['text_zone']
    # 씬 모드: 제목 박스를 위↔아래로 이동 (0.0=상단 기본, 1.0=하단)
    if scene is not None and title_v_frac:
        vf = max(0.0, min(1.0, float(title_v_frac)))
        tz_y = int(60 + vf * max(0, H - tz_h - 120))
    cx = tz_x + tz_w // 2

    if scene is None:
        # ── 캐릭터 슬롯 (그림자 포함, 바닥 정렬) ──────────────────
        char = _load_asset(mascot_src, auto_cut=auto_cut) or _mascot(tpl['char_slot'][2])
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

    # ── 텍스트 존: 뱃지 + 헤드라인 + 서브 ──────────────────────
    d = ImageDraw.Draw(img)

    # 스타일(배너/흰플레이트) 색·외곽선 파라미터
    _banner = (scene is not None and title_style == 'banner')
    if _banner:
        plate_fill = (*_hex_to_rgb(th['headline']), 250)
        hl_color, sub_color = '#FFFFFF', '#FFFFFF'   # 서브도 또렷한 흰색(위계는 크기로)
        sub_size0, sub_stroke = 58, 4
    else:
        plate_fill = (255, 255, 255, 248)
        hl_color, sub_color = th['headline'], th['sub']
        sub_size0, sub_stroke = 60, 5

    # 뱃지 높이
    badge_h = 0
    if badge:
        bf = ImageFont.truetype(_find_korean_font(bold=True), 44)
        bb = d.textbbox((0, 0), badge, font=bf)
        badge_h = (bb[3] - bb[1]) + 44 + 26

    # 서브 사전 계산 — 읽기 좋은 크기 유지, 길면 2줄로 줄바꿈(축소는 최후 수단)
    sub_lines, sf, sub_dy = [], None, 0
    if sub:
        sfp = _find_korean_font(bold=True)
        s_size = sub_size0
        while s_size >= 44:
            sf = ImageFont.truetype(sfp, s_size)
            if len(_wrap(sub, sf, tz_w, d)) <= 2:
                break
            s_size -= 3
        sf = ImageFont.truetype(sfp, s_size)
        sub_lines = _wrap(sub, sf, tz_w, d)[:2]
        sub_dy = int(s_size * 1.18)
    sub_gap = 28 if sub else 0
    sub_h = sub_dy * len(sub_lines)

    # 헤드라인 자동 맞춤 — 크게 유지(서브엔 최소 공간만 양보). 전체가 텍스트 존보다
    # 크면 y가 위로 고정되어 배너가 아래로 자란다.
    hl_box_h = max(150, tz_h - badge_h - (70 if sub else 0))
    fp = _find_display_font()
    hl_lines, hl_font, hl_dy = _fit_headline(
        headline, fp, tz_w, hl_box_h, d, max_size=132, max_lines=2)
    hl_h = hl_dy * len(hl_lines)
    stroke = max(3, round(hl_font.size * 0.10))
    hl_stroke = max(2, round(stroke * 0.6)) if _banner else stroke

    block_h = badge_h + hl_h + sub_gap + sub_h
    y = tz_y + max(0, (tz_h - block_h) // 2)

    # ── 타이틀 플레이트 (블록 전체를 덮게) ──────────────────────
    if scene is not None and title_plate:
        plate_w = 0
        for ln in hl_lines:
            bb = d.textbbox((0, 0), ln, font=hl_font, stroke_width=stroke)
            plate_w = max(plate_w, bb[2] - bb[0])
        # 서브 라인도 반영 — 서브가 헤드라인보다 넓으면 글자가 칸(플레이트)을 벗어나던 버그 수정
        if sf is not None:
            for ln in sub_lines:
                bb = d.textbbox((0, 0), ln, font=sf, stroke_width=sub_stroke)
                plate_w = max(plate_w, bb[2] - bb[0])
        plate_w = int(min(tz_w + 24, plate_w + 100))
        _title_plate(img, cx, y - 30, plate_w, block_h + 56, fill=plate_fill)
        d = ImageDraw.Draw(img)

    if badge:
        bf = ImageFont.truetype(_find_korean_font(bold=True), 44)
        bb = d.textbbox((0, 0), badge, font=bf)
        tw, tht = bb[2] - bb[0], bb[3] - bb[1]
        px0, px1 = cx - tw // 2 - 40, cx + tw // 2 + 40
        d.rounded_rectangle([px0, y, px1, y + tht + 44],
                            radius=(tht + 44) // 2, fill=(*_hex_to_rgb(th['badge']), 255))
        d.text((cx - tw // 2, y + 22 - bb[1]), badge, font=bf, fill=(255, 255, 255, 255))
        y += badge_h

    _draw_center_lines(img, hl_lines, hl_font, cx, y, hl_dy, hl_color, hl_stroke,
                       fallback_path=_find_korean_font(bold=True))
    y += hl_h

    if sub and sub_lines:
        y += sub_gap
        _draw_center_lines(img, sub_lines, sf, cx, y, sub_dy, sub_color, stroke=sub_stroke,
                           fallback_path=_find_korean_font(bold=True))

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
