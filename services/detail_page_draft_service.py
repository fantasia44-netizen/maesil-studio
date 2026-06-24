"""상세페이지 초안 제안서 — PIL 합성 + PNG/PDF 내보내기"""
import io
import logging
import textwrap
import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── 레이아웃 상수 ─────────────────────────────────────────
CANVAS_W   = 1080
IMG_H      = 500       # FLUX 이미지 리사이즈 높이
HEADER_H   = 68
COPY_PAD   = 44
COPY_LINE  = 40        # 카피 줄간격(px)
COPY_FS    = 26        # 카피 폰트 크기
HEAD_FS    = 24        # 섹션명 폰트 크기
PURPOSE_FS = 17
NO_FS      = 15
SECTION_GAP= 10        # 섹션 사이 여백
TITLE_H    = 150

# ── 색상 ─────────────────────────────────────────────────
C_HEADER_BG  = (26,  35,  50)
C_HEADER_FG  = (255, 255, 255)
C_ACCENT     = (75,  92,  222)
C_COPY_BG    = (250, 251, 253)
C_COPY_FG    = (30,  40,  60)
C_PURPOSE    = (155, 165, 185)
C_PLACEHOLDER= (210, 215, 228)
C_WHITE      = (255, 255, 255)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    try:
        from services.imagen_service import _find_korean_font
        return ImageFont.truetype(_find_korean_font(bold=bold), size=size)
    except Exception:
        return ImageFont.load_default()


def _text_w(font: ImageFont.ImageFont, text: str) -> int:
    try:
        bb = font.getbbox(text)
        return bb[2] - bb[0]
    except Exception:
        return len(text) * (font.size if hasattr(font, 'size') else 14)


def _wrap(text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    """폰트 기준 줄바꿈."""
    if not text:
        return []
    paragraphs = text.split('\n')
    lines = []
    for para in paragraphs:
        if not para.strip():
            lines.append('')
            continue
        words = para.split(' ')
        cur = ''
        for word in words:
            test = (cur + ' ' + word).strip()
            if _text_w(font, test) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
    return lines


def _render_section(sec: dict, img_pil: Image.Image | None) -> Image.Image:
    """섹션 1개 이미지 렌더링."""
    f_head    = _font(HEAD_FS,    bold=True)
    f_copy    = _font(COPY_FS,    bold=False)
    f_purpose = _font(PURPOSE_FS, bold=False)
    f_no      = _font(NO_FS,      bold=True)

    copy_text  = sec.get('copy', '')
    copy_lines = _wrap(copy_text, f_copy, CANVAS_W - COPY_PAD * 2)
    copy_h     = max(120, len(copy_lines) * COPY_LINE + COPY_PAD * 2 + 10)

    total_h = HEADER_H + IMG_H + copy_h
    canvas  = Image.new('RGB', (CANVAS_W, total_h), C_WHITE)
    draw    = ImageDraw.Draw(canvas)

    # ── 헤더 바 ──────────────────────────────────────────
    draw.rectangle([(0, 0), (CANVAS_W, HEADER_H)], fill=C_HEADER_BG)

    # 번호 원형
    cx, cy = 40, HEADER_H // 2
    r = 18
    draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], fill=C_ACCENT)
    no_str = str(sec.get('no', 1)).zfill(2)
    nw = _text_w(f_no, no_str)
    draw.text((cx - nw//2, cy - NO_FS//2 - 1), no_str, font=f_no, fill=C_WHITE)

    # 섹션명
    name = sec.get('name', '')
    try:
        nb = f_head.getbbox(name)
        nh = nb[3] - nb[1]
    except Exception:
        nh = HEAD_FS
    draw.text((72, (HEADER_H - nh)//2), name, font=f_head, fill=C_HEADER_FG)

    # purpose (우측 정렬, 흐리게)
    purpose = sec.get('purpose', '')
    if purpose:
        # 너무 길면 자름
        while purpose and _text_w(f_purpose, purpose) > CANVAS_W - 300:
            purpose = purpose[:-2] + '…'
        pw = _text_w(f_purpose, purpose)
        draw.text((CANVAS_W - pw - 20, (HEADER_H - PURPOSE_FS)//2 + 1),
                  purpose, font=f_purpose, fill=C_PURPOSE)

    # ── 스케치 이미지 ─────────────────────────────────────
    y_img = HEADER_H
    if img_pil:
        try:
            resized = img_pil.resize((CANVAS_W, IMG_H), Image.LANCZOS)
            canvas.paste(resized, (0, y_img))
        except Exception as e:
            logger.warning(f'[draft] 이미지 붙이기 실패: {e}')
            _placeholder(draw, 0, y_img, CANVAS_W, IMG_H)
    else:
        _placeholder(draw, 0, y_img, CANVAS_W, IMG_H)

    # ── 카피 영역 ─────────────────────────────────────────
    y_copy = HEADER_H + IMG_H
    draw.rectangle([(0, y_copy), (CANVAS_W, total_h)], fill=C_COPY_BG)
    draw.rectangle([(0, y_copy), (CANVAS_W, y_copy + 4)], fill=C_ACCENT)  # 상단 강조선

    y = y_copy + COPY_PAD
    for line in copy_lines:
        if line == '':
            y += COPY_LINE // 2
            continue
        draw.text((COPY_PAD, y), line, font=f_copy, fill=C_COPY_FG)
        y += COPY_LINE

    return canvas


def _placeholder(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int):
    draw.rectangle([(x, y), (x+w, y+h)], fill=C_PLACEHOLDER)
    f = _font(20)
    msg = '이미지 생성 전'
    mw = _text_w(f, msg)
    draw.text((x + w//2 - mw//2, y + h//2 - 12), msg, font=f, fill=(160, 170, 190))


def _download_image(url: str | None) -> Image.Image | None:
    if not url or not url.startswith('http'):
        return None
    try:
        r = requests.get(url, timeout=25)
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert('RGB')
    except Exception as e:
        logger.warning(f'[draft] 이미지 다운로드 실패 {url}: {e}')
    return None


def compose_draft_png(draft_data: dict) -> bytes:
    """draft output_data → 세로 합성 PNG bytes"""
    product_name = draft_data.get('product_name', '상품명')
    type_name    = draft_data.get('type_name', '')
    sections     = draft_data.get('sections', [])

    f_title    = _font(38, bold=True)
    f_subtitle = _font(21, bold=False)

    # 섹션 이미지 다운로드
    pil_images = [_download_image(s.get('image_url') or '') for s in sections]

    # 섹션 렌더링
    rendered = [_render_section(sec, img) for sec, img in zip(sections, pil_images)]

    # 전체 캔버스 높이
    total_h = TITLE_H + sum(r.height + SECTION_GAP for r in rendered)
    canvas  = Image.new('RGB', (CANVAS_W, total_h), C_WHITE)
    draw    = ImageDraw.Draw(canvas)

    # ── 타이틀 영역 ─────────────────────────────────────
    draw.rectangle([(0, 0), (CANVAS_W, TITLE_H)], fill=C_HEADER_BG)
    draw.rectangle([(0, TITLE_H - 5), (CANVAS_W, TITLE_H)], fill=C_ACCENT)
    draw.text((44, 34), product_name,  font=f_title,    fill=C_WHITE)
    draw.text((44, 88), f'상세페이지 초안 제안서  ·  {type_name}',
              font=f_subtitle, fill=(160, 175, 210))

    # ── 섹션 붙이기 ─────────────────────────────────────
    y = TITLE_H
    for sec_img in rendered:
        canvas.paste(sec_img, (0, y))
        y += sec_img.height + SECTION_GAP

    buf = io.BytesIO()
    canvas.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def compose_draft_pdf(draft_data: dict) -> bytes:
    """PNG를 PDF로 변환 (단일 페이지 tall PDF)."""
    png_bytes = compose_draft_png(draft_data)
    img = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    buf = io.BytesIO()
    img.save(buf, 'PDF', resolution=96.0)
    return buf.getvalue()
