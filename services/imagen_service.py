"""이미지 생성 서비스 — 다중 엔진 라우팅

엔진 선택 전략:
  FLUX.2 Klein  → 프리뷰/저가 (50~100P)
  FLUX.2 Pro    → 고품질 상품·아트 이미지 (300~600P)
  Ideogram 3.0  → 한글 텍스트 포함 썸네일·로고 (400~800P)
  FLUX + PIL    → 긴 한글 문구 합성 카드뉴스 (600~1,200P)
"""
import os
import logging
import requests
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import base64

logger = logging.getLogger(__name__)

# ── 엔진별 포인트 비용 ───────────────────────────────────
IMAGE_COSTS = {
    'flux_preview':  50,   # FLUX.2 Klein — 빠른 시안
    'flux_standard': 300,  # FLUX.2 Pro — 브랜드 에셋
    'flux_hq':       600,  # FLUX.2 Pro Max — 최고 품질
    'ideogram':      400,  # Ideogram 3.0 — 한글 타이포
    'card_news':     800,  # FLUX + PIL 합성
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
                   brand_color: str = None) -> str:
    """이미지 생성 — base64 data URL 또는 https URL 반환"""
    if style_preset and style_preset in STYLE_PRESETS:
        prompt = f'{prompt}, {STYLE_PRESETS[style_preset]}'

    if engine in ('flux_preview', 'flux_standard', 'flux_hq'):
        return _generate_flux(prompt, engine, size)
    elif engine == 'ideogram':
        return _generate_ideogram(prompt, size)
    else:
        return _generate_flux(prompt, 'flux_standard', size)


def generate_card_news(texts: list[str], background_prompt: str,
                       brand_color: str = '#2d8f5e',
                       font_color: str = '#ffffff') -> str:
    """FLUX 배경 + PIL 한글 텍스트 합성 — 카드뉴스"""
    bg_url = _generate_flux(background_prompt, 'flux_standard', '1080x1080')
    return _overlay_text(bg_url, texts, brand_color, font_color)


# ════════════════════════════════════════════════════════
# FLUX.2 (fal.ai)
# ════════════════════════════════════════════════════════

_FAL_MODELS = {
    'flux_preview':  'fal-ai/flux/schnell',      # Klein/Schnell — 고속·저가
    'flux_standard': 'fal-ai/flux-pro',           # Pro — 브랜드 에셋
    'flux_hq':       'fal-ai/flux-pro/v1.1-ultra',# Max — 최고화질
}


def _generate_flux(prompt: str, engine: str, size: str) -> str:
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
    return data['images'][0]['url']


# ════════════════════════════════════════════════════════
# Ideogram 3.0 — 한글 텍스트 포함 이미지
# ════════════════════════════════════════════════════════

def _generate_ideogram(prompt: str, size: str) -> str:
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
    return data['data'][0]['url']


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
    raise FileNotFoundError('한국어 폰트를 찾을 수 없습니다.')


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
