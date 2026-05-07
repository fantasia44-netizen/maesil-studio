"""배너 이미지 생성 서비스

파이프라인: Claude(문구) → FLUX Schnell(배경) → PIL(합성) → PNG 업로드
지원 사이즈: 인스타/스토리/유튜브/OG/카카오/스마트스토어/커스텀
레이아웃: 텍스트 오버레이 / 하단 패널 / 좌우 분할
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import uuid
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════
# 사이즈 & 레이아웃 프리셋
# ════════════════════════════════════════════════════════

BANNER_SIZES: dict[str, dict] = {
    'sns_square':    {'w': 1080, 'h': 1080, 'label': '인스타그램 정사각 (1:1)',  'group': 'SNS'},
    'sns_portrait':  {'w': 1080, 'h': 1350, 'label': '인스타그램 세로 (4:5)',   'group': 'SNS'},
    'sns_story':     {'w': 1080, 'h': 1920, 'label': '스토리 / 릴스 (9:16)',    'group': 'SNS'},
    'youtube_thumb': {'w': 1280, 'h':  720, 'label': '유튜브 썸네일 (16:9)',    'group': '영상'},
    'og_image':      {'w': 1200, 'h':  628, 'label': 'OG 이미지 / SNS 공유',   'group': '웹'},
    'kakao_banner':  {'w': 1024, 'h':  500, 'label': '카카오톡 채널 배너',      'group': '웹'},
    'smartstore':    {'w':  860, 'h':  440, 'label': '스마트스토어 배너',       'group': '쇼핑'},
    'custom':        {'w': None, 'h': None, 'label': '직접 입력',               'group': '기타'},
}

BANNER_LAYOUTS: dict[str, dict] = {
    'overlay':  {
        'label': '텍스트 오버레이',
        'desc':  '전면 이미지 위 하단 그라데이션 텍스트 영역',
        'icon':  'bi-layers-fill',
    },
    'panel': {
        'label': '하단 패널',
        'desc':  '상단 이미지 + 브랜드 컬러 하단 패널',
        'icon':  'bi-layout-split',
    },
    'split_lr': {
        'label': '좌우 분할',
        'desc':  '좌측 브랜드 컬러 텍스트 + 우측 이미지',
        'icon':  'bi-layout-sidebar-reverse',
    },
}

BANNER_BG_TYPES: dict[str, str] = {
    'flux_ai':  'AI 이미지 생성 (FLUX)',
    'solid':    '단색 배경',
    'gradient': '그라데이션 배경',
}


# ════════════════════════════════════════════════════════
# 폰트 유틸 (shorts_service 공용)
# ════════════════════════════════════════════════════════

def _font(bold: bool = False, size: int = 48):
    from services.shorts_service import _font as sf
    return sf(bold=bold, size=size)


def _wrap(text: str, font, max_px: int) -> list[str]:
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
# 이미지 유틸
# ════════════════════════════════════════════════════════

def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _darken(r: int, g: int, b: int, ratio: float = 0.5) -> tuple[int, int, int]:
    return int(r * ratio), int(g * ratio), int(b * ratio)


def _load_img(url: str) -> Image.Image:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert('RGBA')


def _fit_img(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """비율 유지하며 max_w × max_h 안에 맞춤."""
    img.thumbnail((max_w, max_h), Image.LANCZOS)
    return img


def _crop_fill(img: Image.Image, w: int, h: int) -> Image.Image:
    """w×h 크기로 center-crop."""
    img = img.convert('RGB')
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top  = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _make_bg(bg_type: str, w: int, h: int,
             brand_color: str,
             flux_url: str | None = None) -> Image.Image:
    r, g, b = _hex_rgb(brand_color)
    if bg_type == 'flux_ai' and flux_url:
        try:
            return _crop_fill(_load_img(flux_url).convert('RGB'), w, h)
        except Exception as e:
            logger.warning('[banner] 배경 이미지 로드 실패, 단색 폴백: %s', e)
    if bg_type == 'gradient':
        img = Image.new('RGB', (w, h), (r, g, b))
        dr, dg, db = _darken(r, g, b, 0.45)
        draw = ImageDraw.Draw(img)
        for y in range(h):
            ratio = y / h
            cr = int(r + (dr - r) * ratio)
            cg = int(g + (dg - g) * ratio)
            cb = int(b + (db - b) * ratio)
            draw.line([(0, y), (w, y)], fill=(cr, cg, cb))
        return img
    # solid
    return Image.new('RGB', (w, h), (r, g, b))


# ════════════════════════════════════════════════════════
# 배너 합성 — 3개 레이아웃
# ════════════════════════════════════════════════════════

def _draw_text_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    x: int, y: int,
    color: tuple,
    shadow_color: tuple | None = (0, 0, 0, 120),
    line_gap_ratio: float = 1.4,
) -> int:
    """여러 줄 텍스트 그리기. 반환값: 마지막 줄 아래 y 좌표."""
    for ln in lines:
        bb = font.getbbox(ln)
        lh = bb[3] - bb[1]
        if shadow_color:
            draw.text((x + 2, y + 2), ln, font=font, fill=shadow_color)
        draw.text((x, y), ln, font=font, fill=color)
        y += int(lh * line_gap_ratio)
    return y


def _draw_cta_button(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    cta: str,
    cx: int, cy: int,
    brand_color: str,
    font,
) -> None:
    """CTA 버튼 (라운드 사각형 + 텍스트)."""
    if not cta:
        return
    r, g, b = _hex_rgb(brand_color)
    bb = font.getbbox(cta)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    pad_x, pad_y = int(tw * 0.35), int(th * 0.55)
    bx0 = cx - tw // 2 - pad_x
    by0 = cy - th // 2 - pad_y
    bx1 = cx + tw // 2 + pad_x
    by1 = cy + th // 2 + pad_y
    radius = int((by1 - by0) * 0.45)
    # 버튼 배경
    ov = Image.new('RGBA', img.size, (0, 0, 0, 0))
    dov = ImageDraw.Draw(ov)
    dov.rounded_rectangle([(bx0, by0), (bx1, by1)], radius=radius,
                           fill=(r, g, b, 230))
    img.alpha_composite(ov)
    # 텍스트
    draw.text((cx - tw // 2 + 2, cy - th // 2 + 2), cta,
              font=font, fill=(0, 0, 0, 80))
    draw.text((cx - tw // 2, cy - th // 2), cta,
              font=font, fill=(255, 255, 255, 255))


def _composite_overlay(
    bg: Image.Image,
    product_img: Image.Image | None,
    headline: str, subline: str, cta: str,
    brand_color: str,
    W: int, H: int,
) -> Image.Image:
    """전면 이미지 + 하단 그라데이션 텍스트 영역."""
    canvas = bg.convert('RGBA').resize((W, H), Image.LANCZOS)

    # 선택적 제품 이미지 (중앙 상단)
    if product_img:
        ph = int(H * 0.42)
        pi = _fit_img(product_img.copy(), int(W * 0.72), ph)
        px = (W - pi.width) // 2
        py = int(H * 0.05)
        canvas.alpha_composite(pi, dest=(px, py))

    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ov)

    # 하단 그라데이션 오버레이 (60%~100%)
    grad_start = int(H * 0.58)
    for y in range(grad_start, H):
        a = int(220 * (y - grad_start) / (H - grad_start))
        draw.line([(0, y), (W, y)], fill=(8, 8, 8, a))

    # 브랜드 컬러 하단 바
    r, g, b = _hex_rgb(brand_color)
    draw.rectangle([(0, H - 8), (W, H)], fill=(r, g, b, 255))

    canvas.alpha_composite(ov)

    d = ImageDraw.Draw(canvas)
    font_scale = min(W, H)
    hf  = _font(bold=True,  size=max(38, min(76, int(font_scale * 0.058))))
    sf_ = _font(bold=False, size=max(26, min(50, int(font_scale * 0.036))))
    cf  = _font(bold=True,  size=max(22, min(44, int(font_scale * 0.032))))

    text_w = int(W * 0.86)
    margin = int(W * 0.07)
    ty = int(H * 0.63)

    h_lines = _wrap(headline, hf, text_w)[:2]
    ty = _draw_text_lines(d, h_lines, hf, margin, ty,
                          (255, 255, 255, 255), (0, 0, 0, 140))
    ty += int(font_scale * 0.012)

    if subline:
        s_lines = _wrap(subline, sf_, text_w)[:2]
        ty = _draw_text_lines(d, s_lines, sf_, margin, ty,
                              (230, 230, 230, 220), (0, 0, 0, 100))
        ty += int(font_scale * 0.015)

    if cta:
        _draw_cta_button(canvas, ImageDraw.Draw(canvas), cta,
                         cx=int(W * 0.22), cy=ty + int(font_scale * 0.035),
                         brand_color=brand_color, font=cf)

    return canvas.convert('RGB')


def _composite_panel(
    bg: Image.Image,
    product_img: Image.Image | None,
    headline: str, subline: str, cta: str,
    brand_color: str,
    W: int, H: int,
) -> Image.Image:
    """상단 이미지 + 하단 브랜드 컬러 패널."""
    img_h = int(H * 0.60)
    panel_h = H - img_h

    # 상단: 배경 이미지
    top = bg.convert('RGB').resize((W, H), Image.LANCZOS)
    top = top.crop((0, 0, W, img_h))

    # 선택적 제품 이미지 (상단 중앙)
    if product_img:
        ph = int(img_h * 0.80)
        pi = _fit_img(product_img.copy(), int(W * 0.60), ph)
        canvas_top = Image.new('RGBA', (W, img_h), (0, 0, 0, 0))
        canvas_top.paste(top.convert('RGBA'))
        px = (W - pi.width) // 2
        py = (img_h - pi.height) // 2
        canvas_top.alpha_composite(pi, dest=(px, py))
        top = canvas_top.convert('RGB')

    # 하단: 브랜드 컬러 패널
    r, g, b = _hex_rgb(brand_color)
    dr, dg, db = _darken(r, g, b, 0.80)
    panel = Image.new('RGB', (W, panel_h), (r, g, b))
    # 패널 내 미세 그라데이션
    pd = ImageDraw.Draw(panel)
    for y in range(panel_h):
        ratio = y / panel_h
        cr = int(r + (dr - r) * ratio * 0.3)
        cg = int(g + (dg - g) * ratio * 0.3)
        cb = int(b + (db - b) * ratio * 0.3)
        pd.line([(0, y), (W, y)], fill=(cr, cg, cb))

    canvas = Image.new('RGB', (W, H))
    canvas.paste(top,   (0, 0))
    canvas.paste(panel, (0, img_h))

    canvas_rgba = canvas.convert('RGBA')
    d = ImageDraw.Draw(canvas_rgba)

    font_scale = min(W, panel_h * 1.5)
    hf  = _font(bold=True,  size=max(32, min(72, int(font_scale * 0.055))))
    sf_ = _font(bold=False, size=max(22, min(46, int(font_scale * 0.034))))
    cf  = _font(bold=True,  size=max(20, min(40, int(font_scale * 0.030))))

    margin = int(W * 0.07)
    text_w = int(W * 0.86)
    ty = img_h + int(panel_h * 0.12)

    h_lines = _wrap(headline, hf, text_w)[:2]
    ty = _draw_text_lines(d, h_lines, hf, margin, ty,
                          (255, 255, 255, 255), (0, 0, 0, 100))
    ty += int(font_scale * 0.010)

    if subline:
        s_lines = _wrap(subline, sf_, text_w)[:2]
        ty = _draw_text_lines(d, s_lines, sf_, margin, ty,
                              (240, 240, 240, 210), (0, 0, 0, 80))
        ty += int(font_scale * 0.012)

    if cta:
        _draw_cta_button(canvas_rgba, ImageDraw.Draw(canvas_rgba), cta,
                         cx=int(W * 0.20), cy=ty + int(font_scale * 0.030),
                         brand_color='#ffffff', font=cf)

    return canvas_rgba.convert('RGB')


def _composite_split_lr(
    bg: Image.Image,
    product_img: Image.Image | None,
    headline: str, subline: str, cta: str,
    brand_color: str,
    W: int, H: int,
) -> Image.Image:
    """좌우 분할: 좌측 브랜드 컬러 텍스트 + 우측 이미지."""
    split_x = int(W * 0.48)

    # 전체 배경 (FLUX 이미지)
    full_bg = bg.convert('RGBA').resize((W, H), Image.LANCZOS)

    # 좌측 오버레이 (반투명→불투명 그라데이션)
    r, g, b = _hex_rgb(brand_color)
    left_ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(left_ov)
    for x in range(split_x + int(W * 0.10)):
        ratio = min(1.0, x / split_x)
        a = int(240 * (1 - ratio ** 2.5))  # 좌측은 불투명, 우측으로 갈수록 투명
        ld.line([(x, 0), (x, H)], fill=(r, g, b, a))

    canvas = full_bg.copy()
    canvas.alpha_composite(left_ov)

    # 우측 제품 이미지 (선택)
    if product_img:
        right_w = W - split_x
        ph = int(H * 0.78)
        pi = _fit_img(product_img.copy(), int(right_w * 0.78), ph)
        px = split_x + (right_w - pi.width) // 2
        py = (H - pi.height) // 2
        canvas.alpha_composite(pi, dest=(px, py))

    d = ImageDraw.Draw(canvas)
    text_w = int(split_x * 0.80)
    margin = int(split_x * 0.10)
    font_scale = min(split_x, H)

    hf  = _font(bold=True,  size=max(30, min(70, int(font_scale * 0.058))))
    sf_ = _font(bold=False, size=max(20, min(44, int(font_scale * 0.036))))
    cf  = _font(bold=True,  size=max(18, min(38, int(font_scale * 0.030))))

    ty = int(H * 0.25)
    h_lines = _wrap(headline, hf, text_w)[:3]
    ty = _draw_text_lines(d, h_lines, hf, margin, ty,
                          (255, 255, 255, 255), (0, 0, 0, 120))
    ty += int(font_scale * 0.015)

    if subline:
        s_lines = _wrap(subline, sf_, text_w)[:2]
        ty = _draw_text_lines(d, s_lines, sf_, margin, ty,
                              (230, 230, 230, 220), (0, 0, 0, 100))
        ty += int(font_scale * 0.018)

    if cta:
        _draw_cta_button(canvas, ImageDraw.Draw(canvas), cta,
                         cx=margin + int(text_w * 0.38),
                         cy=ty + int(font_scale * 0.040),
                         brand_color=brand_color, font=cf)

    return canvas.convert('RGB')


# ════════════════════════════════════════════════════════
# 메인 합성 진입점
# ════════════════════════════════════════════════════════

def composite_banner(
    bg_type: str,
    bg_url: str | None,
    product_url: str | None,
    headline: str,
    subline: str,
    cta: str,
    brand_color: str,
    layout: str,
    W: int,
    H: int,
) -> str:
    """배너 합성 → JPEG base64 data URI 반환."""
    # 배경
    bg = _make_bg(bg_type, W, H, brand_color, bg_url)

    # 제품 이미지 (선택)
    product_img: Image.Image | None = None
    if product_url:
        try:
            product_img = _load_img(product_url).convert('RGBA')
        except Exception as e:
            logger.warning('[banner] 제품 이미지 로드 실패: %s', e)

    # 레이아웃 선택
    if layout == 'panel':
        result = _composite_panel(bg, product_img, headline, subline, cta, brand_color, W, H)
    elif layout == 'split_lr':
        result = _composite_split_lr(bg, product_img, headline, subline, cta, brand_color, W, H)
    else:  # overlay (default)
        result = _composite_overlay(bg, product_img, headline, subline, cta, brand_color, W, H)

    buf = BytesIO()
    result.convert('RGB').save(buf, 'JPEG', quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'data:image/jpeg;base64,{b64}'


# ════════════════════════════════════════════════════════
# Claude 문구 생성
# ════════════════════════════════════════════════════════

def generate_banner_copy(
    brand_ctx: str,
    purpose: str,
    size_label: str,
    has_product_img: bool,
) -> dict:
    """Claude Haiku → {headline, subline, cta} 반환."""
    from services.claude_service import generate_text

    system = (
        '당신은 디지털 배너 광고 전문 카피라이터입니다. '
        '짧고 강렬한 배너 문구를 작성합니다. '
        '순수 JSON만 출력하세요.'
    )
    prompt = f"""아래 브랜드·상품 정보를 참고해 {size_label} 배너용 문구를 작성하세요.

[브랜드·상품]
{brand_ctx}

[배너 목적]
{purpose or '브랜드/상품 인지도 향상'}

[제품 이미지 포함 여부]
{'있음 — 제품 이미지가 배너에 들어갑니다' if has_product_img else '없음 — 배경과 텍스트만'}

[출력 형식 — 순수 JSON]
{{
  "headline": "핵심 메시지 (15자 이내, 강렬하게)",
  "subline":  "보조 설명 (25자 이내, 혜택/특징 1가지)",
  "cta":      "행동 유도 버튼 텍스트 (8자 이내, 예: 지금 확인하기 / 무료 체험)"
}}

배너는 짧을수록 좋습니다. 숫자·수치가 있으면 적극 활용하세요.
순수 JSON만 출력."""

    raw = generate_text(system, prompt, max_tokens=300, model='claude-haiku-4-5-20251001')
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
    s, e = raw.find('{'), raw.rfind('}') + 1
    if s >= 0 and e > s:
        raw = raw[s:e]
    data = json.loads(raw)
    return {
        'headline': str(data.get('headline', '')),
        'subline':  str(data.get('subline',  '')),
        'cta':      str(data.get('cta',      '')),
    }


# ════════════════════════════════════════════════════════
# 전체 생성 파이프라인 (백그라운드 스레드용)
# ════════════════════════════════════════════════════════

def run_banner_pipeline(
    creation_id: str,
    user_id: str,
    headline: str,
    subline: str,
    cta: str,
    bg_type: str,
    bg_prompt: str,
    brand_color: str,
    layout: str,
    W: int,
    H: int,
    product_url: str | None,
    supabase,
) -> None:
    def _update(status: str, extra: dict | None = None):
        row = {'status': status}
        if extra:
            row['output_data'] = extra
        try:
            supabase.table('creations').update(row).eq('id', creation_id).execute()
        except Exception as e:
            logger.error('[banner] supabase update error: %s', e)

    try:
        bg_url: str | None = None

        # 1) FLUX 배경 생성 (AI 타입만)
        if bg_type == 'flux_ai':
            _update('generating', {'step': 'AI 배경 이미지 생성 중', 'progress': 10})
            from services.imagen_service import _generate_flux
            # FLUX는 최대 1440px 권장, 넘으면 비율 유지해 축소
            flux_w = min(W, 1440)
            flux_h = min(H, 1440)
            if W > 1440 or H > 1440:
                ratio = min(1440 / W, 1440 / H)
                flux_w = int(W * ratio)
                flux_h = int(H * ratio)
            # 짝수로 보정
            flux_w = (flux_w // 2) * 2
            flux_h = (flux_h // 2) * 2
            bg_url, _ = _generate_flux(bg_prompt or 'clean lifestyle photography background, minimal',
                                       'flux_preview', f'{flux_w}x{flux_h}')

        # 2) PIL 합성
        _update('generating', {'step': '이미지 합성 중', 'progress': 60})
        b64_uri = composite_banner(
            bg_type=bg_type,
            bg_url=bg_url,
            product_url=product_url,
            headline=headline,
            subline=subline,
            cta=cta,
            brand_color=brand_color,
            layout=layout,
            W=W,
            H=H,
        )

        # 3) Supabase Storage 업로드
        _update('generating', {'step': '업로드 중', 'progress': 85})
        _, b64data = b64_uri.split(',', 1)
        img_bytes = base64.b64decode(b64data)
        path = f'{user_id}/{uuid.uuid4().hex}_banner.jpg'
        supabase.storage.from_('creations').upload(
            path, img_bytes, {'content-type': 'image/jpeg'}
        )
        image_url = supabase.storage.from_('creations').get_public_url(path)

        _update('done', {'image_url': image_url, 'progress': 100})
        logger.info('[banner] 완료: %s → %s', creation_id, image_url)

    except Exception as e:
        logger.error('[banner] 파이프라인 오류 (%s): %s', creation_id, e)
        _update('failed', {'error': str(e)})
