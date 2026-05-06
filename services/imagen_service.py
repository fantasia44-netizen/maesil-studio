"""이미지 생성 서비스 — 다중 엔진 라우팅

엔진 선택 전략:
  FLUX.2 Klein  → 프리뷰/저가 (50~100P)
  FLUX.2 Pro    → 고품질 상품·아트 이미지 (300~600P)
  Ideogram 3.0  → 한글 텍스트 포함 썸네일·로고 (400~800P)
  FLUX + PIL    → 긴 한글 문구 합성 카드뉴스 (600~1,200P)
"""
import os
import re
import logging
import requests
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import base64

logger = logging.getLogger(__name__)


# ── 한국어 → 영어 번역 (Flux는 한글 이해 불가) ──────────────
_KO_RE = re.compile(r'[가-힣ㄱ-ㆎᄀ-ᇿ一-鿿぀-ヿ㐀-䶿]')  # 한글+CJK 통합

def _has_korean(text: str) -> bool:
    return bool(_KO_RE.search(text))

def _translate_prompt(text: str) -> str:
    """Claude Haiku로 이미지 프롬프트를 영어로 번역. 실패 시 원본 반환.

    규칙:
    - 반드시 순수 영문만 출력 (한글·중국어·일본어 일절 포함 금지)
    - 배경이 아시아/한국 풍이어도 프롬프트 자체는 영어로 작성
    """
    try:
        from services.claude_service import generate_text
        translated = generate_text(
            system=(
                'You are an expert image prompt translator for AI image generation (FLUX model). '
                'Convert the given Korean description into a concise, vivid English image generation prompt. '
                'CRITICAL RULES:\n'
                '- Output ONLY English. Zero Korean, Chinese, or Japanese characters allowed.\n'
                '- Never include any text, letters, or writing instructions in the prompt.\n'
                '- Focus on visual scene: lighting, composition, mood, subject, style.\n'
                '- Output a single line. No explanation, no quotes, no line breaks.'
            ),
            prompt=text,
            max_tokens=300,
            model='claude-haiku-4-5-20251001',
        )
        result = translated.strip().strip('"\'')
        # 번역 결과에 한글이 남아있으면 경고 후 영어 부분만 추출 시도
        if _has_korean(result):
            logger.warning(f'[translate] 번역 결과에 한글 잔존, 재시도: {result[:60]}')
            result = re.sub(r'[가-힣ㄱ-ㆎᄀ-ᇿ\s]+', ' ', result).strip()
        logger.info(f'[translate] KO→EN: "{text[:40]}" → "{result[:60]}"')
        return result or text
    except Exception as e:
        logger.warning(f'[translate] 번역 실패, 원본 사용: {e}')
        return text

# ── 엔진별 포인트 비용 ───────────────────────────────────
IMAGE_COSTS = {
    'flux_preview':  50,   # FLUX Schnell — 빠른 라이프스타일 씬
    'flux_standard': 300,  # FLUX Pro — 브랜드 에셋
    'flux_hq':       600,  # FLUX Pro Max — 최고 품질
    'ideogram':      400,  # Ideogram 3.0 — 한글 타이포
    'card_news':     800,  # FLUX + PIL 합성
    'bg_replace':    80,   # Bria 배경 교체 — 누끼컷 전용
}

# ── 스타일 프리셋 (LoRA 대신 프롬프트로 브랜드 일관성) ──
STYLE_PRESETS = {
    'commercial': 'commercial photography, studio lighting, clean white background, 8k resolution, product focus, professional',
    'webtoon':    'webtoon style, clean line art, soft cel shading, high contrast, korean manhwa aesthetic, vibrant colors',
    'minimal':    'minimalist design, flat illustration, simple shapes, clean composition, modern aesthetic',
    'lifestyle':  'lifestyle photography, natural lighting, warm tones, authentic, candid, aspirational',
}


# ════════════════════════════════════════════════════════
# 메인 진입점
# ════════════════════════════════════════════════════════

def generate_image(prompt: str, engine: str = 'flux_standard',
                   style_preset: str = None, size: str = '1024x1024',
                   brand_color: str = None) -> tuple[str, str]:
    """이미지 생성 — (url, prompt_used) 반환"""
    if style_preset and style_preset in STYLE_PRESETS:
        prompt = f'{prompt}, {STYLE_PRESETS[style_preset]}'

    if engine in ('flux_preview', 'flux_standard', 'flux_hq'):
        return _generate_flux(prompt, engine, size)
    elif engine == 'ideogram':
        return _generate_ideogram(prompt, size)
    else:
        return _generate_flux(prompt, 'flux_standard', size)


def replace_background(image_url: str, bg_prompt: str) -> str:
    """Bria AI 배경 교체 — 누끼컷(제품 컷아웃)의 배경만 교체.

    fal.ai 엔드포인트: fal-ai/bria/background/replace
    제품 패키지·텍스트·로고는 완전히 보존됨.
    bg_prompt: 원하는 배경 설명 (영문)
    """
    from services.config_service import get_config
    api_key = get_config('fal_api_key')
    if not api_key:
        raise ValueError('FAL_KEY가 설정되지 않았습니다.')

    resp = requests.post(
        'https://fal.run/fal-ai/bria/background/replace',
        headers={
            'Authorization': f'Key {api_key}',
            'Content-Type': 'application/json',
        },
        json={
            'image_url': image_url,
            'prompt': bg_prompt,
            'negative_prompt': 'blurry, low quality, distorted',
            'num_images': 1,
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    # Bria 응답 구조: {"images": [{"url": "..."}]} 또는 {"image": {"url": "..."}}
    if data.get('images'):
        return data['images'][0]['url']
    if data.get('image'):
        return data['image']['url']
    raise ValueError(f'Bria 배경 교체 응답 파싱 실패: {data}')


def generate_card_news(texts: list[str], background_prompt: str,
                       brand_color: str = '#2d8f5e',
                       font_color: str = '#ffffff') -> tuple[str, str]:
    """FLUX 배경 + PIL 한글 텍스트 합성 — 카드뉴스"""
    bg_url, prompt_used = _generate_flux(background_prompt, 'flux_standard', '1080x1080')
    return _overlay_text(bg_url, texts, brand_color, font_color), prompt_used


# ════════════════════════════════════════════════════════
# FLUX.2 (fal.ai)
# ════════════════════════════════════════════════════════

_FAL_MODELS = {
    'flux_preview':  'fal-ai/flux/schnell',      # Klein/Schnell — 고속·저가
    'flux_standard': 'fal-ai/flux-pro',           # Pro — 브랜드 에셋
    'flux_hq':       'fal-ai/flux-pro/v1.1-ultra',# Max — 최고화질
}


# Flux는 CJK 문자를 생성하려 할 때 중국어/일본어로 출력하는 경향 — 항상 억제
_NO_CJK = (
    ', no text, no letters, no words, no signs, no labels, no watermarks'
    ', no Chinese characters, no Japanese characters, no Korean characters'
    ', no kanji, no hanzi, no hangul, no CJK glyphs'
    ', absolutely no writing of any language on any surface'
)


def _generate_flux(prompt: str, engine: str, size: str) -> tuple[str, str]:
    """(image_url, prompt_used) 반환. 한글이면 자동 번역."""
    original = prompt
    if _has_korean(prompt):
        prompt = _translate_prompt(prompt)

    # CJK 문자 억제 (Flux가 중국어/일본어 글자를 생성하는 현상 차단)
    prompt = prompt.rstrip() + _NO_CJK

    from services.config_service import get_config
    api_key = get_config('fal_api_key')
    if not api_key:
        raise ValueError('FAL_KEY가 설정되지 않았습니다. 시스템 설정에서 fal_api_key를 등록하세요.')

    w, h = size.split('x')
    model = _FAL_MODELS.get(engine, _FAL_MODELS['flux_standard'])

    resp = requests.post(
        f'https://fal.run/{model}',
        headers={
            'Authorization': f'Key {api_key}',
            'Content-Type': 'application/json',
        },
        json={
            'prompt': prompt,
            'image_size': {'width': int(w), 'height': int(h)},
            'num_images': 1,
            'enable_safety_checker': True,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data['images'][0]['url'], prompt if prompt != original else ''


# ════════════════════════════════════════════════════════
# Ideogram 3.0 — 한글 텍스트 포함 이미지
# ════════════════════════════════════════════════════════

def _generate_ideogram(prompt: str, size: str) -> tuple[str, str]:
    """(image_url, '') 반환. Ideogram은 한글 직접 지원."""
    from services.config_service import get_config
    api_key = get_config('ideogram_api_key')
    if not api_key:
        raise ValueError('IDEOGRAM_API_KEY가 설정되지 않았습니다. 시스템 설정에서 ideogram_api_key를 등록하세요.')

    aspect = '1:1'
    if size == '1920x1080':
        aspect = '16:9'
    elif size == '1080x1920':
        aspect = '9:16'

    resp = requests.post(
        'https://api.ideogram.ai/generate',
        headers={
            'Api-Key': api_key,
            'Content-Type': 'application/json',
        },
        json={
            'image_request': {
                'prompt': prompt,
                'model': 'V_3',          # Ideogram 3.0
                'magic_prompt_option': 'OFF',
                'aspect_ratio': aspect,
                'style_type': 'DESIGN',  # 타이포/디자인 특화
            }
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data['data'][0]['url'], ''


# ════════════════════════════════════════════════════════
# PIL 텍스트 오버레이 — 한글 오타 0% 보장
# ════════════════════════════════════════════════════════

def _overlay_text(image_url: str, texts: list[str],
                  bg_color: str = '#2d8f5e',
                  font_color: str = '#ffffff') -> str:
    """배경 이미지 위에 한글 텍스트 레이어 합성"""
    resp = requests.get(image_url, timeout=30)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content)).convert('RGBA')
    W, H = img.size

    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 반투명 하단 배너
    banner_h = H // 4
    draw.rectangle([(0, H - banner_h), (W, H)],
                   fill=(*_hex_to_rgb(bg_color), 200))

    # 텍스트 렌더링 (시스템 폰트 폴백)
    try:
        font_path = _find_korean_font()
        font_large = ImageFont.truetype(font_path, size=int(H * 0.07))
        font_small = ImageFont.truetype(font_path, size=int(H * 0.04))
    except Exception:
        font_large = ImageFont.load_default()
        font_small = font_large

    y_start = H - banner_h + int(H * 0.03)
    for i, text in enumerate(texts[:3]):
        font = font_large if i == 0 else font_small
        color = _hex_to_rgb(font_color) + (255,)
        draw.text((W * 0.05, y_start), text, font=font, fill=color)
        y_start += int(H * 0.09) if i == 0 else int(H * 0.05)

    combined = Image.alpha_composite(img, overlay)
    buf = BytesIO()
    combined.convert('RGB').save(buf, format='JPEG', quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'data:image/jpeg;base64,{b64}'


def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _find_korean_font() -> str:
    candidates = [
        '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
        '/System/Library/Fonts/AppleSDGothicNeo.ttc',
        'C:/Windows/Fonts/malgun.ttf',
        '/usr/share/fonts/noto/NotoSansCJK-Regular.ttc',
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # 자동 다운로드 (Render 등 클라우드 환경)
    import urllib.request
    dest = '/tmp/NanumGothic.ttf'
    if not os.path.exists(dest):
        logger.info('[font] NanumGothic 자동 다운로드 중...')
        urllib.request.urlretrieve(
            'https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf',
            dest,
        )
    return dest


# ════════════════════════════════════════════════════════
# 상세페이지 섹션 합성 — 소구포인트 3열
# ════════════════════════════════════════════════════════

def generate_feature3_section(
    bg_image_url: str,
    headline: str,
    features: list[dict],   # [{'title': str, 'desc': str}, ...]  최대 3개
    brand_color: str = '#4b5cde',
    font_color: str = '#ffffff',
) -> bytes:
    """소구포인트 3열 섹션 이미지 생성 (800×600 PNG).

    features 예시:
        [
            {'title': '국내산 100%', 'desc': '국내산 야채만 사용'},
            {'title': '40종 야채',   'desc': '브로콜리 외 39종'},
            {'title': '1분 완성',    'desc': '간편한 즉석 조리'},
        ]
    """
    W, H = 800, 600
    features = (features or [])[:3]
    while len(features) < 3:
        features.append({'title': '', 'desc': ''})

    # ── 배경 이미지 로드 ────────────────────────────────
    try:
        resp = requests.get(bg_image_url, timeout=30)
        resp.raise_for_status()
        bg = Image.open(BytesIO(resp.content)).convert('RGBA')
        bg = bg.resize((W, H), Image.LANCZOS)
    except Exception:
        bg = Image.new('RGBA', (W, H), (30, 30, 40, 255))

    # ── 전체 어두운 오버레이 (텍스트 가독성) ───────────────
    dark = Image.new('RGBA', (W, H), (0, 0, 0, 140))
    img = Image.alpha_composite(bg, dark)
    draw = ImageDraw.Draw(img)

    # ── 폰트 로드 ─────────────────────────────────────────
    try:
        fp = _find_korean_font()
        font_head  = ImageFont.truetype(fp, size=36)   # 헤드카피
        font_num   = ImageFont.truetype(fp, size=28)   # 번호 (01/02/03)
        font_title = ImageFont.truetype(fp, size=22)   # 카드 타이틀
        font_desc  = ImageFont.truetype(fp, size=16)   # 카드 설명
    except Exception:
        font_head = font_num = font_title = font_desc = ImageFont.load_default()

    brand_rgb  = _hex_to_rgb(brand_color)
    white      = (255, 255, 255, 255)
    light_gray = (200, 200, 200, 255)

    # ── 헤드카피 (상단 중앙) ─────────────────────────────
    if headline:
        bbox = draw.textbbox((0, 0), headline, font=font_head)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) / 2, 60), headline, font=font_head, fill=white)

    # ── 브랜드 컬러 구분선 ───────────────────────────────
    draw.rectangle([(W * 0.1, 120), (W * 0.9, 123)], fill=(*brand_rgb, 180))

    # ── 3열 카드 ─────────────────────────────────────────
    card_w, card_h = 210, 200
    gap = (W - card_w * 3) // 4          # 균등 간격
    card_y = 160

    for i, feat in enumerate(features):
        cx = gap + i * (card_w + gap)

        # 카드 배경 (반투명 흰색)
        card_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(card_layer)
        cdraw.rounded_rectangle(
            [cx, card_y, cx + card_w, card_y + card_h],
            radius=14,
            fill=(255, 255, 255, 38),    # 15% 불투명 흰색
        )
        img = Image.alpha_composite(img, card_layer)
        draw = ImageDraw.Draw(img)

        # 번호 (01 / 02 / 03) — 브랜드 컬러
        num_text = f'0{i+1}'
        draw.text((cx + 18, card_y + 18), num_text,
                  font=font_num, fill=(*brand_rgb, 255))

        # 브랜드 컬러 짧은 밑줄
        draw.rectangle(
            [(cx + 18, card_y + 55), (cx + 18 + 30, card_y + 58)],
            fill=(*brand_rgb, 200),
        )

        # 타이틀
        title = feat.get('title', '')
        if title:
            draw.text((cx + 18, card_y + 70), title,
                      font=font_title, fill=white)

        # 설명 (줄바꿈 처리)
        desc = feat.get('desc', '')
        if desc:
            _draw_multiline(draw, desc, font_desc, light_gray,
                            cx + 18, card_y + 108, max_width=card_w - 30)

    # ── 하단 브랜드 컬러 바 ──────────────────────────────
    draw.rectangle([(0, H - 8), (W, H)], fill=(*brand_rgb, 255))

    # ── PNG bytes 반환 ───────────────────────────────────
    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def _draw_multiline(draw, text: str, font, fill, x: int, y: int,
                    max_width: int, line_height: int = 22):
    """max_width 넘으면 자동 줄바꿈해서 그리기"""
    words = list(text)  # 한글은 글자 단위
    line, lines = '', []
    for ch in text:
        test = line + ch
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and line:
            lines.append(line)
            line = ch
        else:
            line = test
    if line:
        lines.append(line)
    for i, l in enumerate(lines[:3]):   # 최대 3줄
        draw.text((x, y + i * line_height), l, font=font, fill=fill)


# ════════════════════════════════════════════════════════
# 상세페이지 섹션 합성 — 히어로 오프닝 (800×450)
# ════════════════════════════════════════════════════════

def generate_hero_section(
    bg_image_url: str,
    headline: str,
    subtext: str = '',
    brand_color: str = '#4b5cde',
) -> bytes:
    """상단 히어로 헤더 이미지 (800×450 PNG)."""
    W, H = 800, 450
    try:
        resp = requests.get(bg_image_url, timeout=30)
        resp.raise_for_status()
        bg = Image.open(BytesIO(resp.content)).convert('RGBA').resize((W, H), Image.LANCZOS)
    except Exception:
        bg = Image.new('RGBA', (W, H), (20, 20, 35, 255))

    # 그라데이션 오버레이 (하단 더 어둡게)
    grad = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(H):
        alpha = int(80 + (y / H) * 130)
        gdraw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(bg, grad)
    draw = ImageDraw.Draw(img)

    try:
        fp = _find_korean_font()
        font_h  = ImageFont.truetype(fp, size=46)
        font_s  = ImageFont.truetype(fp, size=22)
    except Exception:
        font_h = font_s = ImageFont.load_default()

    brand_rgb = _hex_to_rgb(brand_color)
    white = (255, 255, 255, 255)

    # 브랜드 컬러 왼쪽 강조선
    draw.rectangle([(60, H // 2 - 50), (66, H // 2 + 50)], fill=(*brand_rgb, 255))

    # 헤드라인 (멀티라인)
    _draw_multiline(draw, headline, font_h, white, 86, H // 2 - 45, max_width=W - 110, line_height=58)

    # 서브텍스트
    if subtext:
        _draw_multiline(draw, subtext, font_s, (200, 200, 220, 220), 86, H // 2 + 30, max_width=W - 110, line_height=30)

    # 하단 브랜드 바
    draw.rectangle([(0, H - 6), (W, H)], fill=(*brand_rgb, 255))

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ════════════════════════════════════════════════════════
# 상세페이지 섹션 합성 — 특장점 단일 (800×500)
# ════════════════════════════════════════════════════════

def generate_feature_highlight(
    bg_image_url: str,
    number: str,        # '01' ~ '09'
    title: str,
    desc: str,
    brand_color: str = '#4b5cde',
    layout: str = 'left',   # 'left': 이미지 왼쪽 | 'right': 이미지 오른쪽
) -> bytes:
    """특장점 단일 강조 이미지 (800×500 PNG). 이미지+텍스트 좌우 분할."""
    W, H = 800, 500
    try:
        resp = requests.get(bg_image_url, timeout=30)
        resp.raise_for_status()
        photo = Image.open(BytesIO(resp.content)).convert('RGBA').resize((W // 2, H), Image.LANCZOS)
    except Exception:
        photo = Image.new('RGBA', (W // 2, H), (30, 30, 45, 255))

    img = Image.new('RGBA', (W, H), (18, 18, 30, 255))
    brand_rgb = _hex_to_rgb(brand_color)

    # 텍스트 패널에 브랜드 컬러 미세 그라데이션
    panel_x = W // 2 if layout == 'left' else 0
    for x in range(W // 2):
        alpha = int(8 + x * 0.06)
        for y_line in range(0, H, 4):
            img.putpixel((panel_x + x, min(y_line, H - 1)),
                         (*brand_rgb, min(alpha, 30)))

    # 사진 붙이기
    photo_x = 0 if layout == 'left' else W // 2
    img.paste(photo, (photo_x, 0))

    # 사진 안쪽 그라데이션 (자연스러운 경계)
    fade = Image.new('RGBA', (60, H), (0, 0, 0, 0))
    fdraw = ImageDraw.Draw(fade)
    for x in range(60):
        a = int((1 - x / 60) * 180)
        fdraw.line([(x, 0), (x, H)], fill=(18, 18, 30, a))
    fade_x = (W // 2 - 60) if layout == 'left' else W // 2
    img = Image.alpha_composite(img, Image.new('RGBA', (W, H), (0, 0, 0, 0)))
    img.paste(photo, (photo_x, 0))

    draw = ImageDraw.Draw(img)

    # 페이드 경계 (간단히 직접 그리기)
    if layout == 'left':
        for xi in range(40):
            a = int((1 - xi / 40) * 160)
            draw.line([(W // 2 - 40 + xi, 0), (W // 2 - 40 + xi, H)], fill=(18, 18, 30, a))
    else:
        for xi in range(40):
            a = int(xi / 40 * 160)
            draw.line([(W // 2 + xi, 0), (W // 2 + xi, H)], fill=(18, 18, 30, a))

    try:
        fp = _find_korean_font()
        font_num   = ImageFont.truetype(fp, size=52)
        font_title = ImageFont.truetype(fp, size=28)
        font_desc  = ImageFont.truetype(fp, size=17)
    except Exception:
        font_num = font_title = font_desc = ImageFont.load_default()

    tx = W // 2 + 40 if layout == 'left' else 40
    white = (255, 255, 255, 255)
    gray  = (180, 185, 210, 255)

    # 번호 (브랜드 컬러, 반투명)
    draw.text((tx, 90), number, font=font_num, fill=(*brand_rgb, 180))
    # 브랜드 컬러 짧은 선
    draw.rectangle([(tx, 175), (tx + 40, 179)], fill=(*brand_rgb, 255))
    # 타이틀
    _draw_multiline(draw, title, font_title, white, tx, 192, max_width=320, line_height=38)
    # 설명
    _draw_multiline(draw, desc, font_desc, gray, tx, 270, max_width=320, line_height=26)

    # 하단 브랜드 바
    draw.rectangle([(0, H - 5), (W, H)], fill=(*brand_rgb, 255))

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ════════════════════════════════════════════════════════
# 상세페이지 섹션 합성 — 텍스트 강조 (800×320)
# ════════════════════════════════════════════════════════

def generate_text_emphasis(
    main_text: str,
    sub_text: str = '',
    brand_color: str = '#4b5cde',
) -> bytes:
    """브랜드 컬러 배경 텍스트 강조 이미지 (800×320 PNG). 배경 이미지 불필요."""
    W, H = 800, 320
    brand_rgb = _hex_to_rgb(brand_color)

    # 브랜드 컬러 어두운 그라데이션 배경
    img = Image.new('RGBA', (W, H), (*brand_rgb, 255))
    draw = ImageDraw.Draw(img)
    # 어두운 오버레이
    for y in range(H):
        alpha = int(120 + (y / H) * 60)
        draw.line([(0, y), (W, y)], fill=(0, 0, 10, alpha))

    # 장식 원 (반투명)
    draw.ellipse([(-60, -60), (180, 180)], fill=(*brand_rgb, 40))
    draw.ellipse([(W - 180, H - 180), (W + 60, H + 60)], fill=(*brand_rgb, 40))

    try:
        fp = _find_korean_font()
        font_main = ImageFont.truetype(fp, size=38)
        font_sub  = ImageFont.truetype(fp, size=20)
    except Exception:
        font_main = font_sub = ImageFont.load_default()

    white     = (255, 255, 255, 255)
    off_white = (220, 225, 240, 200)

    # 인용부호 장식
    draw.text((50, 50), '❝', font=font_main, fill=(255, 255, 255, 60))

    # 메인 텍스트 (중앙 정렬)
    lines = []
    line = ''
    for ch in main_text:
        test = line + ch
        bbox = draw.textbbox((0, 0), test, font=font_main)
        if bbox[2] - bbox[0] > W - 120 and line:
            lines.append(line)
            line = ch
        else:
            line = test
    if line:
        lines.append(line)
    lines = lines[:3]

    total_h = len(lines) * 52
    start_y = (H - total_h) // 2 - (20 if sub_text else 0)
    for i, l in enumerate(lines):
        bbox = draw.textbbox((0, 0), l, font=font_main)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, start_y + i * 52), l, font=font_main, fill=white)

    # 서브텍스트
    if sub_text:
        bbox = draw.textbbox((0, 0), sub_text, font=font_sub)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, start_y + total_h + 16), sub_text, font=font_sub, fill=off_white)

    # 상하 흰색 얇은 선
    draw.rectangle([(80, 22), (W - 80, 25)], fill=(255, 255, 255, 60))
    draw.rectangle([(80, H - 25), (W - 80, H - 22)], fill=(255, 255, 255, 60))

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ════════════════════════════════════════════════════════
# 상세페이지 섹션 합성 — CTA 마무리 (800×380)
# ════════════════════════════════════════════════════════

def generate_cta_section(
    bg_image_url: str,
    cta_text: str,
    sub_text: str = '',
    brand_color: str = '#4b5cde',
) -> bytes:
    """CTA(구매 촉구) 마무리 이미지 (800×380 PNG)."""
    W, H = 800, 380
    try:
        resp = requests.get(bg_image_url, timeout=30)
        resp.raise_for_status()
        bg = Image.open(BytesIO(resp.content)).convert('RGBA').resize((W, H), Image.LANCZOS)
    except Exception:
        bg = Image.new('RGBA', (W, H), (15, 15, 25, 255))

    brand_rgb = _hex_to_rgb(brand_color)

    # 강한 어두운 오버레이
    dark = Image.new('RGBA', (W, H), (0, 0, 10, 175))
    img = Image.alpha_composite(bg, dark)

    # 브랜드 컬러 하단 그라데이션
    grad = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(H // 2, H):
        a = int(((y - H // 2) / (H // 2)) * 120)
        gdraw.line([(0, y), (W, y)], fill=(*brand_rgb, a))
    img = Image.alpha_composite(img, grad)

    draw = ImageDraw.Draw(img)

    try:
        fp = _find_korean_font()
        font_cta = ImageFont.truetype(fp, size=40)
        font_sub = ImageFont.truetype(fp, size=20)
        font_btn = ImageFont.truetype(fp, size=22)
    except Exception:
        font_cta = font_sub = font_btn = ImageFont.load_default()

    white = (255, 255, 255, 255)
    off_white = (210, 215, 235, 220)

    # CTA 텍스트 중앙
    lines = []
    line = ''
    for ch in cta_text:
        test = line + ch
        bbox = draw.textbbox((0, 0), test, font=font_cta)
        if bbox[2] - bbox[0] > W - 120 and line:
            lines.append(line)
            line = ch
        else:
            line = test
    if line:
        lines.append(line)
    lines = lines[:2]

    total_h = len(lines) * 54
    start_y = H // 2 - total_h // 2 - 20
    for i, l in enumerate(lines):
        bbox = draw.textbbox((0, 0), l, font=font_cta)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, start_y + i * 54), l, font=font_cta, fill=white)

    # 서브텍스트
    if sub_text:
        bbox = draw.textbbox((0, 0), sub_text, font=font_sub)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, start_y + total_h + 12), sub_text, font=font_sub, fill=off_white)

    # 버튼 모양 장식
    btn_text = '지금 바로 구매하기 →'
    bbox = draw.textbbox((0, 0), btn_text, font=font_btn)
    bw = bbox[2] - bbox[0] + 60
    bh = 44
    bx = (W - bw) // 2
    by = H - 80
    # 버튼 배경
    btn_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(btn_layer)
    bdraw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=22,
                             fill=(*brand_rgb, 220), outline=(255, 255, 255, 100), width=1)
    img = Image.alpha_composite(img, btn_layer)
    draw = ImageDraw.Draw(img)
    draw.text((bx + 30, by + 10), btn_text, font=font_btn, fill=white)

    # 상단 브랜드 바
    draw.rectangle([(0, 0), (W, 5)], fill=(*brand_rgb, 255))

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ════════════════════════════════════════════════════════
# Supabase Storage 업로드
# ════════════════════════════════════════════════════════

def upload_to_supabase(image_data: str, user_id: str, filename: str) -> str:
    """이미지(URL 또는 base64 data URL) → Supabase Storage → 공개 URL"""
    from flask import current_app
    supabase = current_app.supabase
    if not supabase:
        return image_data

    import uuid
    path = f'{user_id}/{uuid.uuid4()}_{filename}'
    bucket = 'creations'

    if image_data.startswith('data:image/'):
        header, b64data = image_data.split(',', 1)
        raw = base64.b64decode(b64data)
        mime = header.split(';')[0].split(':')[1]
        supabase.storage.from_(bucket).upload(path, raw, {'content-type': mime})
    else:
        r = requests.get(image_data, timeout=30)
        r.raise_for_status()
        mime = r.headers.get('Content-Type', 'image/png')
        supabase.storage.from_(bucket).upload(path, r.content, {'content-type': mime})

    return supabase.storage.from_(bucket).get_public_url(path)
