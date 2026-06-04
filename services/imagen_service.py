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
            model='claude-sonnet-4-6',
        )
        result = translated.strip().strip('"\'')
        # 번역 결과에 한글이 남아있으면 CJK 문자 제거 후 영어 부분만 사용
        if _has_korean(result):
            logger.warning(f'[translate] 번역 결과에 한글 잔존, 스트립: {result[:60]}')
            result = re.sub(r'[가-힣ㄱ-ㆎᄀ-ᇿ一-鿿぀-ヿ㐀-䶿]+', ' ', result)
            result = re.sub(r'\s+', ' ', result).strip()
        logger.info(f'[translate] KO→EN: "{text[:40]}" → "{result[:60]}"')
        return result  # 빈 문자열 반환 가능 → _generate_flux에서 처리
    except Exception as e:
        logger.warning(f'[translate] 번역 실패: {e}')
        return ''  # 빈 문자열 반환 — 한글을 그대로 fal에 보내지 않음

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
    ', no speech bubbles, no word balloons, no dialogue bubbles, no comic balloons'
    ', absolutely no writing of any language on any surface'
)


def _generate_flux(prompt: str, engine: str, size: str) -> tuple[str, str]:
    """(image_url, prompt_used) 반환. 한글이면 자동 번역."""
    original = prompt
    if _has_korean(prompt):
        translated = _translate_prompt(prompt)
        if translated and not _has_korean(translated):
            # 정상 번역
            prompt = translated
        elif translated:
            # 번역 결과에 한글이 남아있음 — CJK 강제 제거
            cleaned = re.sub(r'[가-힣ㄱ-ㆎᄀ-ᇿ一-鿿぀-ヿ㐀-䶿]+', ' ', translated)
            prompt = re.sub(r'\s+', ' ', cleaned).strip() or 'lifestyle scene'
        else:
            # 번역 완전 실패(빈 문자열) — 원본 한글에서 CJK 제거 후 안전 기본값 사용
            cleaned = re.sub(r'[가-힣ㄱ-ㆎᄀ-ᇿ一-鿿぀-ヿ㐀-䶿]+', ' ', prompt)
            prompt = re.sub(r'\s+', ' ', cleaned).strip() or 'lifestyle scene, natural lighting'
        logger.debug(f'[flux] 번역 후 프롬프트: "{prompt[:80]}"')

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
            'negative_prompt': (
                'deformed hands, extra fingers, missing fingers, fused fingers, '
                'too many fingers, mutated hands, bad anatomy, extra limbs, '
                'malformed limbs, missing arms, missing legs, extra arms, extra legs, '
                'cloned face, disfigured, ugly, gross proportions, long neck, '
                'bad proportions, watermark, signature, text, logo, '
                'chart, bar chart, pie chart, line chart, graph, data visualization, '
                'infographic, diagram, financial chart, stock chart, candlestick chart, '
                'flowchart, screen with graphs, tablet with charts, monitor with data, '
                'floating UI overlay, dashboard, spreadsheet'
            ),
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
    if len(h) != 6:
        return (75, 92, 222)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _shadow_text(draw, pos, text, font, fill=(255,255,255,255),
                 shadow_color=(0,0,0,160), offset=2):
    """드롭 섀도우가 적용된 텍스트 그리기."""
    draw.text((pos[0]+offset, pos[1]+offset), text, font=font, fill=shadow_color)
    draw.text((pos[0]-1,     pos[1]-1),     text, font=font, fill=shadow_color)
    draw.text(pos, text, font=font, fill=fill)


def _shadow_multiline(draw, text, font, fill, x, y, max_width,
                      line_height=28, shadow_color=(0,0,0,160)):
    """멀티라인 + 드롭 섀도우."""
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
    for i, l in enumerate(lines[:4]):
        yy = y + i * line_height
        draw.text((x+2, yy+2), l, font=font, fill=shadow_color)
        draw.text((x,   yy),   l, font=font, fill=fill)


def _draw_gradient_rect(img, x0, y0, x1, y1, color_top, color_bot):
    """수직 그라데이션 직사각형 (RGBA)."""
    draw = ImageDraw.Draw(img)
    h = y1 - y0
    for dy in range(h):
        t = dy / max(h, 1)
        r = int(color_top[0]*(1-t) + color_bot[0]*t)
        g = int(color_top[1]*(1-t) + color_bot[1]*t)
        b = int(color_top[2]*(1-t) + color_bot[2]*t)
        a = int(color_top[3]*(1-t) + color_bot[3]*t)
        draw.line([(x0, y0+dy), (x1, y0+dy)], fill=(r, g, b, a))


def _fit_lines(font_path: str, text: str, base_size: int,
               max_w: int, max_lines: int):
    """단어(공백) 기준 줄바꿈 → 폰트 축소(15%씩 최대 4회) 순으로 시도.

    기존 문자 단위 줄바꿈은 'vs' → 'v'/'s' 처럼 영단어 중간에서 잘리는 문제 발생.
    공백 기준 단어 단위로 먼저 시도하고, 공백 없는 텍스트는 문자 단위 폴백 사용.
    Returns (lines, font) where font may be smaller than base_size.
    """
    def _measure(f, s):
        try:
            return f.getbbox(s)[2] - f.getbbox(s)[0]
        except Exception:
            return len(s) * f.size if hasattr(f, 'size') else len(s) * 16

    for shrink in range(5):
        size = max(16, int(base_size * (0.85 ** shrink)))
        try:
            f = ImageFont.truetype(font_path, size=size)
        except Exception:
            f = ImageFont.load_default()

        # ── 단어(공백) 단위 줄바꿈 ──────────────────────────────
        tokens  = text.split(' ')
        lines   = []
        current = ''
        for tok in tokens:
            candidate = (current + ' ' + tok).lstrip()
            if _measure(f, candidate) > max_w and current:
                lines.append(current)
                current = tok
            else:
                current = candidate
        if current:
            lines.append(current)

        if len(lines) <= max_lines:
            return lines, f

    # ── 최소 폰트로도 max_lines 초과 시: 마지막 폰트로 자름 ──────
    # (공백 없는 긴 텍스트 등 극단적 케이스 — 문자 단위 폴백)
    size = max(16, int(base_size * (0.85 ** 4)))
    try:
        f = ImageFont.truetype(font_path, size=size)
    except Exception:
        f = ImageFont.load_default()
    lines, line = [], ''
    for ch in text:
        test = line + ch
        if _measure(f, test) > max_w and line:
            lines.append(line)
            line = ch
        else:
            line = test
    if line:
        lines.append(line)
    return lines[:max_lines], f


def _topic_to_bg_scene(topic: str) -> str:
    """블로그 주제(한글/영문) → FLUX 배경 장면 설명 변환.

    한글 예시를 시스템 프롬프트에 직접 포함해 번역 오류 방지.
    차트·화면·데이터 묘사 완전 금지.
    """
    try:
        from services.claude_service import generate_text
        result = generate_text(
            system=(
                'Convert a blog topic keyword into a SHORT physical background scene '
                'for a FLUX image generation prompt.\n'
                '\n'
                'RULES:\n'
                '- Describe ONLY a real physical place, objects, and lighting\n'
                '- NEVER use: chart, graph, data, screen, monitor, tablet, computer, '
                'whiteboard, dashboard, infographic, spreadsheet, presentation, statistics\n'
                '- If input is Korean, translate it AND describe the scene\n'
                '- Output: 6-12 English words ONLY. No quotes. No explanation.\n'
                '\n'
                'EXAMPLES (Korean → scene):\n'
                '물류센터 창고 → large warehouse interior tall metal shelves industrial lighting\n'
                '3pl물류센터 → fulfillment center aisle cardboard boxes industrial overhead light\n'
                '쿠팡 물류 → large distribution center interior conveyor belt industrial\n'
                '종합소득세 → paper tax documents on wooden desk lamp light close-up\n'
                'ETF 투자 → gold coins stacked dark surface shallow depth bokeh\n'
                '부동산 → modern apartment building exterior blue hour twilight\n'
                '건강기능식품 → herbal capsules wooden surface natural light bokeh\n'
                '다이어트 → fresh vegetables on cutting board natural kitchen light\n'
                '육아 → colorful toys on soft carpet warm cozy room\n'
                'EXAMPLES (English → scene):\n'
                'logistics → warehouse aisle metal shelves cardboard boxes industrial\n'
                'finance → coins and banknotes dark background bokeh\n'
                'food → ingredients on kitchen counter natural light'
            ),
            prompt=topic,
            max_tokens=50,
            model='claude-sonnet-4-6',
        )
        scene = result.strip().strip('"\'').rstrip('.').strip()
        # 차트 관련 단어 후처리 제거
        for bad in ('chart','graph','data','screen','monitor','tablet','dashboard',
                    'spreadsheet','whiteboard','presentation','infographic','diagram',
                    'statistics','analytics','display'):
            scene = scene.replace(bad, '').strip()
        return scene if len(scene) > 5 else 'dark atmospheric industrial interior dramatic lighting'
    except Exception as e:
        logger.warning(f'[topic_to_bg] 변환 실패: {e}')
        return 'dark atmospheric cinematic background bokeh'


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
    W, H = 1080, 720
    features = (features or [])[:3]
    while len(features) < 3:
        features.append({'title': '', 'desc': ''})

    brand_rgb = _hex_to_rgb(brand_color)
    white     = (255, 255, 255, 255)
    gray200   = (200, 205, 220, 255)

    # ── 배경 이미지 로드 ────────────────────────────────
    try:
        resp = requests.get(bg_image_url, timeout=30)
        resp.raise_for_status()
        bg = Image.open(BytesIO(resp.content)).convert('RGBA').resize((W, H), Image.LANCZOS)
    except Exception:
        bg = Image.new('RGBA', (W, H), (22, 22, 35, 255))
        # 브랜드 컬러 미묘한 그라데이션 배경
        _draw_gradient_rect(bg, 0, 0, W, H,
            (*brand_rgb, 40), (0, 0, 0, 0))

    # ── 레이어드 오버레이 ────────────────────────────────
    overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    _draw_gradient_rect(overlay, 0, 0, W, H, (0,0,0,100), (0,0,10,190))
    img = Image.alpha_composite(bg, overlay)

    # ── 상단 브랜드 컬러 띠 ──────────────────────────────
    top_bar = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    tbdraw = ImageDraw.Draw(top_bar)
    tbdraw.rectangle([(0, 0), (W, 5)], fill=(*brand_rgb, 255))
    img = Image.alpha_composite(img, top_bar)

    draw = ImageDraw.Draw(img)

    # ── 폰트 로드 ─────────────────────────────────────────
    try:
        fp = _find_korean_font()
        font_label = ImageFont.truetype(fp, size=14)
        font_head  = ImageFont.truetype(fp, size=42)
        font_num   = ImageFont.truetype(fp, size=36)
        font_title = ImageFont.truetype(fp, size=24)
        font_desc  = ImageFont.truetype(fp, size=17)
    except Exception:
        font_label = font_head = font_num = font_title = font_desc = ImageFont.load_default()

    # ── 영문 서브레이블 (상단) ───────────────────────────
    label_text = 'KEY FEATURES'
    label_bbox = draw.textbbox((0,0), label_text, font=font_label)
    lw = label_bbox[2] - label_bbox[0]
    draw.text(((W - lw)//2, 28), label_text, font=font_label,
              fill=(*brand_rgb, 200))

    # ── 헤드카피 중앙 정렬 + 드롭섀도 ──────────────────
    if headline:
        head_bbox = draw.textbbox((0, 0), headline, font=font_head)
        tw = head_bbox[2] - head_bbox[0]
        _shadow_text(draw, ((W - tw)//2, 54), headline, font_head,
                     fill=white, shadow_color=(0,0,0,180), offset=3)

    # ── 헤드라인 하단 장식선 ─────────────────────────────
    line_y = 118
    line_cx = W // 2
    draw.rectangle([(line_cx - 30, line_y), (line_cx + 30, line_y + 3)],
                   fill=(*brand_rgb, 255))

    # ── 3열 카드 ─────────────────────────────────────────
    CARD_W, CARD_H = 288, 280
    GAP  = (W - CARD_W * 3) // 4
    CARD_TOP = 150

    for i, feat in enumerate(features):
        cx = GAP + i * (CARD_W + GAP)

        # 카드 배경 — 미묘한 반투명
        cl = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(cl)
        cd.rounded_rectangle(
            [cx, CARD_TOP, cx + CARD_W, CARD_TOP + CARD_H],
            radius=18,
            fill=(255, 255, 255, 22),
            outline=(*brand_rgb, 80),
            width=1,
        )
        img = Image.alpha_composite(img, cl)
        draw = ImageDraw.Draw(img)

        inner_x = cx + 24
        inner_y = CARD_TOP + 24

        # 번호 대형 장식 (반투명 배경)
        num_text = f'0{i+1}'
        num_bbox = draw.textbbox((0,0), num_text, font=font_num)
        nw = num_bbox[2] - num_bbox[0]
        _shadow_text(draw, (inner_x, inner_y), num_text, font_num,
                     fill=(*brand_rgb, 255), shadow_color=(0,0,0,120), offset=2)

        # 브랜드 컬러 구분선 (번호 아래)
        bar_y = inner_y + 52
        draw.rectangle([(inner_x, bar_y), (inner_x + 36, bar_y + 3)],
                       fill=(*brand_rgb, 255))

        # 타이틀 + 드롭섀도
        title = feat.get('title', '')
        if title:
            _shadow_text(draw, (inner_x, bar_y + 14), title, font_title,
                         fill=white, shadow_color=(0,0,0,150), offset=2)

        # 설명
        desc = feat.get('desc', '')
        if desc:
            _shadow_multiline(draw, desc, font_desc, gray200,
                              inner_x, bar_y + 52, max_width=CARD_W - 48,
                              line_height=26)

    # ── 하단 브랜드 그라데이션 바 ────────────────────────
    bot = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    _draw_gradient_rect(bot, 0, H-8, W, H, (*brand_rgb, 255), (*brand_rgb, 255))
    img = Image.alpha_composite(img, bot)

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
    """상단 히어로 헤더 이미지 (1080×540 PNG)."""
    W, H = 1080, 540
    brand_rgb = _hex_to_rgb(brand_color)
    white = (255, 255, 255, 255)

    try:
        resp = requests.get(bg_image_url, timeout=30)
        resp.raise_for_status()
        bg = Image.open(BytesIO(resp.content)).convert('RGBA').resize((W, H), Image.LANCZOS)
    except Exception:
        bg = Image.new('RGBA', (W, H), (15, 15, 25, 255))
        _draw_gradient_rect(bg, 0, 0, W, H, (*brand_rgb, 60), (0, 0, 0, 0))

    # 레이어드 오버레이: 상단 밝고 하단 어둡게
    ov = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(ov, 0, 0, W, H, (0,0,0,60), (0,0,0,200))
    img = Image.alpha_composite(bg, ov)

    # 좌측 브랜드 컬러 세로 그라데이션 띠
    side = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(side, 0, 0, 8, H, (*brand_rgb,255), (*brand_rgb,255))
    img = Image.alpha_composite(img, side)
    draw = ImageDraw.Draw(img)

    try:
        fp = _find_korean_font()
        font_label = ImageFont.truetype(fp, size=15)
        font_h     = ImageFont.truetype(fp, size=56)
        font_s     = ImageFont.truetype(fp, size=24)
    except Exception:
        font_label = font_h = font_s = ImageFont.load_default()

    # 브랜드 서브레이블
    draw.text((80, H//2 - 80), 'DETAIL PAGE', font=font_label,
              fill=(*brand_rgb, 200))

    # 메인 헤드라인
    _shadow_multiline(draw, headline, font_h, white, 80, H//2 - 52,
                      max_width=W - 160, line_height=68)

    # 서브텍스트
    if subtext:
        # 브랜드 컬러 구분선
        draw.rectangle([(80, H//2 + 38), (80+50, H//2+41)], fill=(*brand_rgb,255))
        _shadow_multiline(draw, subtext, font_s, (200,205,225,230),
                          80, H//2 + 52, max_width=W-160, line_height=34,
                          shadow_color=(0,0,0,120))

    # 하단 브랜드 바
    bot = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(bot, 0, H-6, W, H, (*brand_rgb,255), (*brand_rgb,255))
    img = Image.alpha_composite(img, bot)

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
    """특장점 단일 강조 이미지 (1080×600 PNG). 좌우 분할."""
    W, H = 1080, 600
    HALF = W // 2
    brand_rgb = _hex_to_rgb(brand_color)
    white = (255, 255, 255, 255)
    gray  = (185, 190, 215, 255)

    # ── 다크 베이스 ──────────────────────────────────────
    img = Image.new('RGBA', (W, H), (16, 16, 28, 255))

    # ── 텍스트 패널 브랜드 그라데이션 ───────────────────
    txt_x = HALF if layout == 'left' else 0
    panel = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(panel, txt_x, 0, txt_x + HALF, H,
                        (*brand_rgb, 18), (*brand_rgb, 8))
    img = Image.alpha_composite(img, panel)

    # ── 사진 로드 + 붙이기 ───────────────────────────────
    photo_x = 0 if layout == 'left' else HALF
    try:
        resp = requests.get(bg_image_url, timeout=30)
        resp.raise_for_status()
        photo = Image.open(BytesIO(resp.content)).convert('RGBA').resize((HALF, H), Image.LANCZOS)
        # 사진에 약한 오버레이
        phov = Image.new('RGBA', (HALF, H), (0,0,0,40))
        photo = Image.alpha_composite(photo, phov)
        img.paste(photo.convert('RGB'), (photo_x, 0))
    except Exception:
        grad_bg = Image.new('RGBA', (HALF, H), (25, 25, 40, 255))
        _draw_gradient_rect(grad_bg, 0, 0, HALF, H, (*brand_rgb,30), (0,0,0,0))
        img.paste(grad_bg.convert('RGB'), (photo_x, 0))

    # ── 경계 페이드 오버레이 ─────────────────────────────
    fade_layer = Image.new('RGBA', (W, H), (0,0,0,0))
    fade_draw  = ImageDraw.Draw(fade_layer)
    fade_w = 80
    if layout == 'left':
        for xi in range(fade_w):
            a = int((1 - xi/fade_w)**1.5 * 220)
            fade_draw.line([(HALF - fade_w + xi, 0),(HALF - fade_w + xi, H)],
                           fill=(16,16,28,a))
    else:
        for xi in range(fade_w):
            a = int((xi/fade_w)**1.5 * 220)
            fade_draw.line([(HALF + xi, 0),(HALF + xi, H)], fill=(16,16,28,a))
    img = Image.alpha_composite(img.convert('RGBA'), fade_layer)
    draw = ImageDraw.Draw(img)

    # ── 폰트 ─────────────────────────────────────────────
    try:
        fp = _find_korean_font()
        font_num   = ImageFont.truetype(fp, size=64)
        font_title = ImageFont.truetype(fp, size=34)
        font_desc  = ImageFont.truetype(fp, size=19)
    except Exception:
        font_num = font_title = font_desc = ImageFont.load_default()

    tx = HALF + 60 if layout == 'left' else 60
    max_tw = HALF - 100

    # 번호 (크고 반투명)
    _shadow_text(draw, (tx, H//2 - 140), number, font_num,
                 fill=(*brand_rgb, 200), shadow_color=(0,0,0,100), offset=3)
    # 구분선
    draw.rectangle([(tx, H//2 - 52), (tx+50, H//2-48)], fill=(*brand_rgb, 255))
    # 타이틀
    _shadow_multiline(draw, title, font_title, white,
                      tx, H//2 - 36, max_width=max_tw, line_height=44)
    # 설명
    _shadow_multiline(draw, desc, font_desc, gray,
                      tx, H//2 + 26, max_width=max_tw, line_height=30)

    # 하단 바
    bot = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(bot, 0, H-5, W, H, (*brand_rgb,255), (*brand_rgb,255))
    img = Image.alpha_composite(img, bot)

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
    """브랜드 컬러 배경 텍스트 강조 이미지 (1080×400 PNG)."""
    W, H = 1080, 400
    brand_rgb = _hex_to_rgb(brand_color)
    white     = (255, 255, 255, 255)
    off_white = (215, 220, 240, 220)

    # 깊은 다크 + 브랜드 그라데이션 배경
    img = Image.new('RGBA', (W, H), (12, 12, 22, 255))
    _draw_gradient_rect(img, 0, 0, W, H, (*brand_rgb, 70), (*brand_rgb, 20))

    # 좌우 장식 세로선
    sl = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(sl, 0, 0, 4, H, (*brand_rgb, 0), (*brand_rgb, 255))
    _draw_gradient_rect(sl, W-4, 0, W, H, (*brand_rgb, 255), (*brand_rgb, 0))
    img = Image.alpha_composite(img, sl)

    # 상하 가는 라인
    ln = Image.new('RGBA', (W, H), (0,0,0,0))
    lnd = ImageDraw.Draw(ln)
    lnd.rectangle([(80, 28), (W-80, 30)], fill=(255,255,255,50))
    lnd.rectangle([(80, H-30), (W-80, H-28)], fill=(255,255,255,50))
    img = Image.alpha_composite(img, ln)
    draw = ImageDraw.Draw(img)

    try:
        fp = _find_korean_font()
        font_sub = ImageFont.truetype(fp, size=22)
    except Exception:
        fp = None
        font_sub = ImageFont.load_default()

    # 메인 텍스트 — 적응형 폰트: 3줄 이내에 맞을 때까지 15%씩 축소
    if fp:
        lines, font_main = _fit_lines(fp, main_text, 46, W - 160, 3)
        lh = max(1, font_main.getbbox('가')[3] - font_main.getbbox('가')[1])
        line_gap = int(lh * 1.38)
    else:
        font_main = ImageFont.load_default()
        lines = [main_text]
        line_gap = 60

    total_h = len(lines) * line_gap
    start_y = (H - total_h) // 2 - (18 if sub_text else 0)
    for i, l in enumerate(lines):
        bbox = draw.textbbox((0, 0), l, font=font_main)
        tw = bbox[2] - bbox[0]
        _shadow_text(draw, ((W - tw) // 2, start_y + i * line_gap), l, font_main,
                     fill=white, shadow_color=(0, 0, 0, 160), offset=3)

    # 서브텍스트
    if sub_text:
        bbox = draw.textbbox((0,0), sub_text, font=font_sub)
        tw = bbox[2]-bbox[0]
        # 작은 구분선
        draw.rectangle([((W-20)//2, start_y+total_h+8), ((W+20)//2, start_y+total_h+11)],
                       fill=(*brand_rgb,255))
        _shadow_text(draw, ((W-tw)//2, start_y+total_h+18), sub_text, font_sub,
                     fill=off_white, shadow_color=(0,0,0,120), offset=2)

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
    """CTA(구매 촉구) 마무리 이미지 (1080×500 PNG)."""
    W, H = 1080, 500
    brand_rgb = _hex_to_rgb(brand_color)
    white     = (255, 255, 255, 255)
    off_white = (210, 218, 240, 220)

    try:
        resp = requests.get(bg_image_url, timeout=30)
        resp.raise_for_status()
        bg = Image.open(BytesIO(resp.content)).convert('RGBA').resize((W, H), Image.LANCZOS)
    except Exception:
        bg = Image.new('RGBA', (W, H), (12, 12, 22, 255))
        _draw_gradient_rect(bg, 0, 0, W, H, (*brand_rgb, 50), (0,0,0,0))

    # 강한 어두운 오버레이 + 브랜드 하단 그라데이션
    ov = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(ov, 0, 0, W, H, (0,0,0,120), (0,0,0,200))
    img = Image.alpha_composite(bg, ov)
    brand_glow = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(brand_glow, 0, H//2, W, H, (0,0,0,0), (*brand_rgb, 100))
    img = Image.alpha_composite(img, brand_glow)

    # 상단 브랜드 바
    top = Image.new('RGBA', (W, H), (0,0,0,0))
    _draw_gradient_rect(top, 0, 0, W, 5, (*brand_rgb,255), (*brand_rgb,255))
    img = Image.alpha_composite(img, top)
    draw = ImageDraw.Draw(img)

    try:
        fp = _find_korean_font()
        font_sub = ImageFont.truetype(fp, size=22)
        font_btn = ImageFont.truetype(fp, size=24)
    except Exception:
        fp = None
        font_sub = font_btn = ImageFont.load_default()

    # CTA 텍스트 — 적응형 폰트: 2줄 이내에 맞을 때까지 15%씩 축소
    if fp:
        lines, font_cta = _fit_lines(fp, cta_text, 50, W - 160, 2)
        lh = max(1, font_cta.getbbox('가')[3] - font_cta.getbbox('가')[1])
        line_gap = int(lh * 1.38)
    else:
        font_cta = ImageFont.load_default()
        lines = [cta_text]
        line_gap = 66

    total_h = len(lines) * line_gap
    start_y = H // 2 - total_h // 2 - 30
    for i, l in enumerate(lines):
        bbox = draw.textbbox((0, 0), l, font=font_cta)
        tw = bbox[2] - bbox[0]
        _shadow_text(draw, ((W - tw) // 2, start_y + i * line_gap), l, font_cta,
                     fill=white, shadow_color=(0, 0, 0, 180), offset=3)

    if sub_text:
        bbox = draw.textbbox((0,0), sub_text, font=font_sub)
        tw = bbox[2]-bbox[0]
        _shadow_text(draw, ((W-tw)//2, start_y+total_h+10), sub_text, font_sub,
                     fill=off_white, shadow_color=(0,0,0,120), offset=2)

    # 구매하기 버튼
    btn_text = '지금 바로 구매하기 →'
    bbox = draw.textbbox((0,0), btn_text, font=font_btn)
    bw = bbox[2]-bbox[0]+80; bh = 52
    bx = (W-bw)//2; by = H - 95
    bl = Image.new('RGBA', (W, H), (0,0,0,0))
    bd = ImageDraw.Draw(bl)
    bd.rounded_rectangle([bx, by, bx+bw, by+bh], radius=26,
                          fill=(*brand_rgb, 230), outline=(255,255,255,120), width=2)
    img = Image.alpha_composite(img, bl)
    draw = ImageDraw.Draw(img)
    _shadow_text(draw, (bx+40, by+13), btn_text, font_btn,
                 fill=white, shadow_color=(0,0,0,100), offset=2)

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


# ════════════════════════════════════════════════════════
# Supabase Storage 업로드
# ════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════
# 블로그 썸네일 카드 — 제목 텍스트 오버레이 (1080×1080)
# ════════════════════════════════════════════════════════

def _make_dark_gradient_bg(W: int, H: int, accent_rgb: tuple) -> Image.Image:
    """PIL로 다크 그라데이션 배경 (FLUX 없이, 무료)."""
    bg = Image.new('RGBA', (W, H), (8, 12, 25, 255))
    _draw_gradient_rect(bg, 0, 0, W, H, (*accent_rgb, 28), (0, 0, 0, 0))
    _draw_gradient_rect(bg, 0, H // 2, W, H, (0, 0, 0, 0), (*accent_rgb, 18))
    # 미묘한 대각 스트라이프 — 질감 표현
    d = ImageDraw.Draw(bg)
    for i in range(0, W + H, 90):
        d.line([(i, 0), (0, i)], fill=(255, 255, 255, 5), width=1)
    return bg


def generate_blog_thumbnail(
    line1: str,
    line2: str = '',
    background_url: str | None = None,
    brand_name: str = '',
    accent_color: str = '#FFD700',
    line1_color: str = '#FFFFFF',     # 줄1 글자색
    use_quotes: bool = True,
    text_y_pct: int = 55,
    font_size_pct: int = 115,
    overlay_darkness: int = 78,
    text_align: str = 'center',
    letter_spacing: int = 0,           # 자간 (픽셀, 음수=좁힘)
    text_bg_color: str = '',           # 글자 배경색 (빈 문자열=없음)
    text_bg_opacity: int = 60,         # 글자 배경 불투명도 (0-100)
) -> bytes:
    """블로그 썸네일 카드 생성 (1080×1080 PNG) — 비주얼 에디터 연동.

    Parameters
    ----------
    text_y_pct       : 텍스트 시작 Y 위치 % (0=최상단, 100=최하단)
    font_size_pct    : 폰트 크기 배율 (100=기본, 80=작게, 120=크게)
    overlay_darkness : 하단 어두운 정도 0-100 → alpha 0-250 매핑
    text_align       : 텍스트 정렬 방향
    """
    W = H = 1080
    accent_rgb = _hex_to_rgb(accent_color)
    scale = max(0.5, min(1.5, font_size_pct / 100))

    # ── 배경 ─────────────────────────────────────────────
    if background_url:
        try:
            if background_url.startswith('data:image/'):
                # base64 data URL (직접 업로드)
                _, b64data = background_url.split(',', 1)
                raw = base64.b64decode(b64data)
                bg = Image.open(BytesIO(raw)).convert('RGBA').resize((W, H), Image.LANCZOS)
            else:
                # 일반 HTTP URL
                resp = requests.get(background_url, timeout=30)
                resp.raise_for_status()
                bg = Image.open(BytesIO(resp.content)).convert('RGBA').resize((W, H), Image.LANCZOS)
        except Exception:
            bg = _make_dark_gradient_bg(W, H, accent_rgb)
    else:
        bg = _make_dark_gradient_bg(W, H, accent_rgb)

    # ── 오버레이: 슬라이더 값에 따라 전체적으로 어둡게 ──────────
    # 슬라이더 84%인데 상단이 10%만 어둡던 비대칭 문제 수정.
    # 상단도 슬라이더 값의 ~70%까지 반영하여 전체 시각적 어둠 강화.
    top_alpha = max(20, min(255, int(overlay_darkness * 1.7)))   # ≈ slider*0.68 비율
    mid_alpha = max(40, min(255, int(overlay_darkness * 2.1)))   # 중간(텍스트 영역) 강하게
    bot_alpha = max(60, min(255, int(overlay_darkness * 2.5)))   # 하단 가장 어둡게
    pivot = int(H * 0.35)
    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    _draw_gradient_rect(ov, 0, 0,      W, pivot, (0,0,0,top_alpha), (0,0,0,mid_alpha))
    _draw_gradient_rect(ov, 0, pivot,  W, H,     (0,0,0,mid_alpha), (0,0,0,bot_alpha))
    img = Image.alpha_composite(bg, ov)
    draw = ImageDraw.Draw(img)

    # ── 폰트 ─────────────────────────────────────────────
    try:
        fp = _find_korean_font()
        sz1 = int(H * 0.120 * scale)   # 10.8% → 12% (네이버 카드 가독성 개선)
        sz2 = int(H * 0.104 * scale)   # 9.4%  → 10.4%
        szm = int(H * 0.036)
        font_l1   = ImageFont.truetype(fp, size=max(24, sz1))
        font_l2   = ImageFont.truetype(fp, size=max(20, sz2))
        font_mark = ImageFont.truetype(fp, size=max(16, szm))
    except Exception:
        fp = None
        font_l1 = font_l2 = font_mark = ImageFont.load_default()

    l1_rgb    = _hex_to_rgb(line1_color)
    line1_fill = (*l1_rgb, 255)
    accent    = (*accent_rgb, 255)

    # 정렬에 따른 좌우 여백
    MARGIN = 80
    MAX_TW = W - MARGIN * 2

    def _lh(font):
        try:
            return font.getbbox('가')[3] - font.getbbox('가')[1]
        except Exception:
            return int(sz1)

    def _measure_spaced(draw, text, font, spacing):
        # 자간 적용된 총 너비 계산
        total = 0
        for c in text:
            try:
                cw = draw.textbbox((0,0), c, font=font)[2]
            except Exception:
                cw = font.size if hasattr(font,'size') else 20
            total += cw + spacing
        return max(0, total - spacing)

    def _draw_spaced(draw, x, y, text, font, fill, shadow_color, offset, spacing):
        # 자간 적용 문자별 렌더링
        for c in text:
            _shadow_text(draw, (x, y), c, font, fill, shadow_color, offset)
            try:
                cw = draw.textbbox((0,0), c, font=font)[2]
            except Exception:
                cw = font.size if hasattr(font,'size') else 20
            x += cw + spacing

    def _x(tw):
        if text_align == 'left':   return MARGIN
        if text_align == 'right':  return W - MARGIN - tw
        return (W - tw) // 2  # center

    # ── 인용부호 ─────────────────────────────────────────
    if use_quotes:
        render_l1 = '”' + line1
        render_l2 = (line2 + '”') if line2 else ''
        if not line2:
            render_l1 = '”' + line1 + '”'
    else:
        render_l1 = line1
        render_l2 = line2

    # ── 텍스트 줄 계산 ────────────────────────────────────
    def _wrap(text, font, sz):
        if not fp or not text:
            return [], font
        return _fit_lines(fp, text, sz, MAX_TW, 2)

    l1_lines, font_l1 = _wrap(render_l1, font_l1, max(24, sz1 if fp else 80))
    l2_lines, font_l2 = _wrap(render_l2, font_l2, max(20, sz2 if fp else 70)) if render_l2 else ([], font_l2)

    gap = 1.30
    dy1  = int(_lh(font_l1) * gap)
    dy2  = int(_lh(font_l2) * gap)
    h1   = dy1 * max(len(l1_lines), 1)
    h2   = dy2 * max(len(l2_lines), 1) if l2_lines else 0
    LINE_GAP = max(8, int(H * 0.016))
    MARK_PAD = int(H * 0.11)
    total_h  = h1 + (LINE_GAP + h2 if l2_lines else 0)

    y_start_raw = int(H * text_y_pct / 100)
    y = max(int(H * 0.05), min(y_start_raw, H - MARK_PAD - total_h - 20))

    # ── 글자 배경 강조 (text highlight box) ──────────────
    if text_bg_color and text_bg_opacity > 0:
        bg_rgb = _hex_to_rgb(text_bg_color)
        bg_alpha = int(text_bg_opacity * 2.55)
        PAD_X, PAD_Y = 28, 14
        # 전체 텍스트 블록 너비 기준 배경 박스
        all_lines = l1_lines + l2_lines
        all_fonts = [font_l1]*len(l1_lines) + [font_l2]*len(l2_lines)
        max_tw = 0
        for ln, fnt in zip(all_lines, all_fonts):
            if letter_spacing:
                tw = _measure_spaced(draw, ln, fnt, letter_spacing)
            else:
                try:
                    tw = draw.textbbox((0,0), ln, font=fnt)[2]
                except Exception:
                    tw = 0
            max_tw = max(max_tw, tw)
        box_w = max_tw + PAD_X * 2
        box_h = total_h + PAD_Y * 2
        box_x = _x(max_tw) - PAD_X
        box_y = y - PAD_Y
        # 반투명 사각형 레이어
        bg_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        bg_draw  = ImageDraw.Draw(bg_layer)
        try:
            bg_draw.rounded_rectangle(
                [box_x, box_y, box_x + box_w, box_y + box_h],
                radius=16, fill=(*bg_rgb, bg_alpha)
            )
        except Exception:
            bg_draw.rectangle(
                [box_x, box_y, box_x + box_w, box_y + box_h],
                fill=(*bg_rgb, bg_alpha)
            )
        img = Image.alpha_composite(img, bg_layer)
        draw = ImageDraw.Draw(img)

    # ── Line 1 ────────────────────────────────────────────
    for l in l1_lines:
        if letter_spacing:
            tw = _measure_spaced(draw, l, font_l1, letter_spacing)
            _draw_spaced(draw, _x(tw), y, l, font_l1,
                         line1_fill, (0,0,0,225), 5, letter_spacing)
        else:
            bbox = draw.textbbox((0, 0), l, font=font_l1)
            tw = bbox[2] - bbox[0]
            _shadow_text(draw, (_x(tw), y), l, font_l1,
                         fill=line1_fill, shadow_color=(0,0,0,225), offset=5)
        y += dy1

    # ── Line 2 ────────────────────────────────────────────
    if l2_lines:
        y += LINE_GAP
        for l in l2_lines:
            if letter_spacing:
                tw = _measure_spaced(draw, l, font_l2, letter_spacing)
                _draw_spaced(draw, _x(tw), y, l, font_l2,
                             accent, (0,0,0,225), 5, letter_spacing)
            else:
                bbox = draw.textbbox((0, 0), l, font=font_l2)
                tw = bbox[2] - bbox[0]
                _shadow_text(draw, (_x(tw), y), l, font_l2,
                             fill=accent, shadow_color=(0,0,0,225), offset=5)
            y += dy2

    # ── 워터마크 ──────────────────────────────────────────
    if brand_name:
        mark = brand_name if brand_name.startswith('@') else f'@{brand_name}'
        bbox = draw.textbbox((0, 0), mark, font=font_mark)
        tw = bbox[2] - bbox[0]
        draw.text((_x(tw), H - int(H * 0.075)),
                  mark, font=font_mark, fill=(255,255,255,140))

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()


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
