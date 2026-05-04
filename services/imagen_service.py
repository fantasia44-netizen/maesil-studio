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
_KO_RE = re.compile(r'[가-힣ㄱ-ㆎᄀ-ᇿ]')

def _has_korean(text: str) -> bool:
    return bool(_KO_RE.search(text))

def _translate_prompt(text: str) -> str:
    """Claude Haiku로 이미지 프롬프트를 영어로 번역. 실패 시 원본 반환."""
    try:
        from services.claude_service import generate_text
        translated = generate_text(
            system=(
                'You are an expert image prompt translator. '
                'Translate the given Korean image description into a concise, vivid English image generation prompt. '
                'Output ONLY the English prompt. No explanation, no quotes, no line breaks.'
            ),
            prompt=text,
            max_tokens=300,
            model='claude-haiku-4-5-20251001',
        )
        result = translated.strip().strip('"\'')
        logger.info(f'[translate] KO→EN: "{text[:40]}" → "{result[:60]}"')
        return result
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
    ', no Chinese characters, no Japanese characters, no kanji, no hanzi, '
    'no CJK text on signs or labels, Latin alphabet only for any visible text'
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
