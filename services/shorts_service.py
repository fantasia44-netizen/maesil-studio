"""쇼츠/릴스 영상 자동 생성 파이프라인

구조: 훅 → 공감 → 해결 → 핵심혜택 → CTA  (5씬, 7~20초)
엔진: FLUX Schnell(이미지) + Google TTS(나레이션) + FFmpeg(조립)
"""
from __future__ import annotations

import base64
import glob as _glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── 씬 역할 정의 ─────────────────────────────────────────────
SCENE_ROLES = [
    ('hook',     '훅',       '시청자 시선 즉시 포착 — 충격적 질문/수치/반전 (2~3초)'),
    ('empathy',  '공감',     '타겟의 문제/불편함 공감 — "이런 경험 있으신가요?" (3~4초)'),
    ('solution', '해결',     '제품/서비스가 어떻게 해결하는지 (3~4초)'),
    ('benefit',  '핵심혜택', '가장 강력한 한 가지 혜택/차별점 (3~4초)'),
    ('cta',      'CTA',      '구체적 행동 유도 — 링크/댓글/팔로우 (2~3초)'),
]

# ── 이미지 스타일 프리셋 ────────────────────────────────────
SHORTS_STYLE_PRESETS = {
    'realistic_banner': (
        'cinematic lifestyle photography, 9:16 vertical frame, 35mm lens f/1.8 shallow depth of field, '
        'warm golden-hour bokeh, soft directional window light, rich muted color palette, '
        'Korean aesthetic, editorial commercial quality, ultra-sharp foreground, '
        'professional color grading, clean modern composition'
    ),
    'webtoon': (
        'Korean webtoon manhwa illustration, bold confident line art with clean outlines, '
        'vibrant saturated colors with cel shading, dynamic vertical panel composition, '
        'expressive characters with detailed facial features, professional comic art quality, '
        'dramatic lighting with rim light accents, high contrast shadows'
    ),
    'ghibli': (
        'Studio Ghibli hand-painted watercolor animation, lush detailed natural backgrounds, '
        'soft warm pastel color palette, gentle dreamy atmosphere, delicate painterly textures, '
        'Hayao Miyazaki inspired, 9:16 vertical composition, golden sunlight through foliage, '
        'cinematic wide shot, professional animation quality'
    ),
    'flat_modern': (
        'modern flat design vector illustration, bold geometric color blocks, '
        'clean editorial graphic style, Scandinavian minimalist aesthetic, '
        'professional brand identity quality, strong visual hierarchy, '
        'carefully chosen duotone color palette, sophisticated negative space, 9:16 vertical frame'
    ),
    'disney': (
        'Pixar Disney 3D CGI animation style, vibrant polished photorealistic render, '
        'expressive rounded characters, warm cinematic three-point lighting, '
        'subsurface skin scattering, rich volumetric light rays, '
        'family-friendly commercial quality, ultra-detailed, 9:16 vertical frame'
    ),
}

# 강력한 텍스트·해부학 네거티브 프롬프트
_NO_TEXT = (
    ', no text, no letters, no words, no captions, no subtitles, no signs, no labels, '
    'no Chinese characters, no Japanese characters, no Korean characters, no kanji, no hanzi, no hangul, '
    'no CJK text, no watermark, no typography, no writing, no inscriptions'
)
_NO_ANATOMY = (
    ', correct human anatomy, exactly five fingers on each hand, natural hand proportions, '
    'no extra limbs, no deformed hands, no missing fingers, no six fingers, realistic anatomy'
)
# FLUX Schnell 퀄리티 최대화 suffix — 비용 추가 없이 품질 향상
_QUALITY_SUFFIX = (
    ', masterpiece, best quality, highly detailed, professional photography, '
    'sharp focus, high resolution, perfect composition, award-winning'
)


# ════════════════════════════════════════════════════════
# 1. 대본 생성
# ════════════════════════════════════════════════════════

def generate_shorts_script(
    brand_ctx: str,
    angle: dict,
    style: str = 'realistic_banner',
    visual_mode: str = 'scene_mood',
    product_name: str = '',
) -> list[dict]:
    """Claude로 5씬 쇼츠 대본 생성.

    visual_mode:
      'product_focus' — 각 씬에 실제 제품이 시각적으로 등장 (화장품·의료기기 등)
      'scene_mood'    — 감성 배경/라이프스타일 위주, 제품 직접 노출 없음 (특화식품 등)

    Returns: [
      {role, role_ko, narration, overlay_title, overlay_body, flux_prompt}, ...
    ]
    """
    from services.claude_service import generate_text

    angle_title = angle.get('title', '') if isinstance(angle, dict) else ''
    angle_vibe  = angle.get('image_vibe', '') if isinstance(angle, dict) else ''
    angle_hook  = angle.get('hook', '') if isinstance(angle, dict) else ''

    style_guide = SHORTS_STYLE_PRESETS.get(style, SHORTS_STYLE_PRESETS['realistic_banner'])

    scenes_desc = '\n'.join(
        f'- scene {i+1} "{r[1]}" ({r[0]}): {r[2]}'
        for i, r in enumerate(SCENE_ROLES)
    )

    angle_problem  = angle.get('problem',  '') if isinstance(angle, dict) else ''
    angle_solution = angle.get('solution', '') if isinstance(angle, dict) else ''
    angle_result   = angle.get('result',   '') if isinstance(angle, dict) else ''

    if visual_mode == 'product_focus':
        visual_instruction = f"""[이미지 방향 — 제품 중심]
각 씬의 flux_prompt에 실제 제품({product_name or '브랜드 제품'})이 화면에 직접 등장하도록 묘사하세요.
제품의 외관·패키지·사용 장면을 구체적으로 설명하세요.
scene 5(CTA)는 제품 단독 클로즈업 또는 제품을 들고 있는 장면으로 작성하세요."""
    else:
        visual_instruction = f"""[이미지 방향 — 감성 배경 중심]
각 씬의 flux_prompt는 라이프스타일·분위기·감성을 담은 배경 이미지로 작성하세요.
제품을 화면에 직접 보여주지 않고, 제품이 주는 감정·생활 방식·환경으로 표현하세요.
scene 5(CTA)는 브랜드 감성을 담은 라이프스타일 장면으로 작성하세요."""

    system = '당신은 숏폼 영상 전문 크리에이터입니다. 순수 JSON만 출력하세요.'
    prompt = f"""인스타 릴스/유튜브 쇼츠용 5씬 대본을 JSON으로 생성하세요.
아래 소구포인트의 문제-해결 서사를 씬 전체에 일관되게 관통시키세요.

[브랜드·상품]
{brand_ctx}

[소구포인트 — 이 서사를 중심으로 대본을 구성하세요]
- 제목: {angle_title}
- 타겟의 문제/불편: {angle_problem}
- 후킹 문구: {angle_hook}
- 상품의 해결 방식: {angle_solution}
- 해결 후 변화/결과: {angle_result}
- 영상 분위기: {angle_vibe}

[씬별 역할 — 총 15~25초]
{scenes_desc}

[이미지 스타일]
{style_guide}

{visual_instruction}

[flux_prompt 필수 규칙]
- 영문만 사용, 65~90 단어 (더 구체적일수록 품질 향상)
- 9:16 vertical frame, full frame composition (피사체 잘림 없이 전체 포함)
- 한글·한자·일본어·아랍어 등 어떤 문자도 절대 포함 금지
- 사람이 등장할 경우 자연스러운 손 묘사 (손가락 5개)
- 5씬 전체가 동일한 색조·조명·무드를 유지해야 함 (시각적 연속성)
- 씬 역할별 카메라 앵글 변화: hook=클로즈업/드라마틱, empathy=미디엄샷/감성적, solution=제품+인물, benefit=밝고 자신감있는, cta=와이드/브랜드샷
- 조명·색조·렌즈 특성을 반드시 명시 (예: "soft rim light, f/2.8 shallow DOF, warm golden tones")

[출력 형식 — 순수 JSON 배열]
[
  {{
    "role": "hook",
    "narration": "나레이션 텍스트 (한글, 2~4초 분량, 10~25자)",
    "overlay_title": "화면 상단 굵은 텍스트 (한글, 12자 이내)",
    "overlay_body": "화면 하단 자막 텍스트 (한글, narration과 동일 또는 축약)",
    "flux_prompt": "영문 FLUX 이미지 프롬프트"
  }},
  ...5개 씬...
]

순수 JSON 배열만 출력."""

    raw = generate_text(system, prompt, max_tokens=1600, model='claude-sonnet-4-6')
    clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
    s, e = clean.find('['), clean.rfind(']') + 1
    if s >= 0 and e > s:
        clean = clean[s:e]
    scenes = json.loads(clean)

    for i, sc in enumerate(scenes[:5]):
        sc['role']    = SCENE_ROLES[i][0]
        sc['role_ko'] = SCENE_ROLES[i][1]

    return scenes[:5]


# ════════════════════════════════════════════════════════
# 2. Google TTS
# ════════════════════════════════════════════════════════

VOICE_OPTIONS = {
    'female_natural': ('ko-KR', 'ko-KR-Neural2-A',  'Neural2'),
    'male_calm':      ('ko-KR', 'ko-KR-Neural2-C',  'Neural2'),
    'female_bright':  ('ko-KR', 'ko-KR-Neural2-B',  'Neural2'),
    'male_clear':     ('ko-KR', 'ko-KR-Wavenet-C',  'Wavenet'),
    'female_studio':  ('ko-KR', 'ko-KR-Studio-B',   'Studio'),  # 최고급 자연스러운 여성
    'male_studio':    ('ko-KR', 'ko-KR-Studio-O',   'Studio'),  # 최고급 자연스러운 남성
}

# 씬 역할별 TTS 파라미터 — 감정·속도 변화로 생동감 향상
_SCENE_TTS = {
    'hook':     {'pitch': +1.5, 'rate_delta': +0.08},   # 훅: 약간 높고 빠르게
    'empathy':  {'pitch': -0.5, 'rate_delta': -0.06},   # 공감: 낮고 차분하게
    'solution': {'pitch':  0.0, 'rate_delta':  0.00},   # 해결: 기본
    'benefit':  {'pitch': +1.0, 'rate_delta': +0.04},   # 혜택: 밝고 자신있게
    'cta':      {'pitch': -1.0, 'rate_delta': -0.04},   # CTA: 낮고 신뢰감있게
}


# TTS 발음 교정 — 영문 약어를 한글 발음으로 치환
_TTS_REPLACEMENTS = [
    ('ROAS',  '로아스'),   # 광고수익률
    ('ROI',   '알오아이'),
    ('SNS',   '에스엔에스'),
    ('SaaS',  '사스'),
    ('B2B',   '비투비'),
    ('B2C',   '비투씨'),
    ('MOQ',   '모크'),
    ('AI',    '에이아이'),
    ('CTA',   '씨티에이'),
    ('KPI',   '케이피아이'),
    ('SEO',   '에스이오'),
    ('CPM',   '씨피엠'),
    ('CPC',   '씨피씨'),
    ('URL',   '유알엘'),
    ('QR',    '큐알'),
]

def _normalize_tts_text(text: str) -> str:
    """TTS 발음이 어색한 영문 약어를 한글 발음으로 변환."""
    import re as _re
    for eng, kor in _TTS_REPLACEMENTS:
        text = _re.sub(rf'\b{eng}\b', kor, text, flags=_re.IGNORECASE)
    return text


def tts_synthesize(text: str, api_key: str,
                   voice_key: str = 'female_natural',
                   speed: float = 1.1,
                   scene_role: str = '') -> bytes:
    """Google TTS REST API → MP3 bytes.

    scene_role: 씬 역할('hook'/'empathy'/'solution'/'benefit'/'cta')
                전달 시 역할별 피치·속도 자동 보정으로 생동감 향상.
    """
    lang, name, model = VOICE_OPTIONS.get(voice_key, VOICE_OPTIONS['female_natural'])

    # 씬 역할별 피치·속도 보정
    scene_cfg = _SCENE_TTS.get(scene_role, {'pitch': 0.0, 'rate_delta': 0.0})
    pitch     = scene_cfg['pitch']
    final_spd = round(max(0.7, min(1.5, speed + scene_cfg['rate_delta'])), 2)

    # Studio 음성은 v1beta1 API 사용
    api_ver  = 'v1beta1' if model == 'Studio' else 'v1'
    endpoint = f'https://texttospeech.googleapis.com/{api_ver}/text:synthesize?key={api_key}'

    resp = requests.post(
        endpoint,
        json={
            'input': {'text': text},
            'voice': {'languageCode': lang, 'name': name},
            'audioConfig': {
                'audioEncoding':    'MP3',
                'speakingRate':     final_spd,
                'pitch':            pitch,
                'volumeGainDb':     1.5,
                'effectsProfileId': ['headphone-class-device'],
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    b64 = resp.json().get('audioContent', '')
    if not b64:
        raise ValueError('Google TTS 응답에 audioContent가 없습니다.')
    return base64.b64decode(b64)


# ════════════════════════════════════════════════════════
# 3. 이미지 프레임 합성 (PIL)
# ════════════════════════════════════════════════════════

_FONT_URLS = {
    'NanumGothic.ttf':     'https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf',
    'NanumGothicBold.ttf': 'https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Bold.ttf',
}

def _ensure_font(fname: str) -> str | None:
    """static/fonts/ 에 폰트가 없으면 자동 다운로드 후 경로 반환."""
    here     = os.path.dirname(os.path.abspath(__file__))
    font_dir = os.path.join(here, '..', 'static', 'fonts')
    dest     = os.path.join(font_dir, fname)
    if os.path.exists(dest):
        return dest
    url = _FONT_URLS.get(fname)
    if not url:
        return None
    try:
        os.makedirs(font_dir, exist_ok=True)
        import urllib.request
        urllib.request.urlretrieve(url, dest)
        logger.info('[font] 다운로드 완료: %s', dest)
        return dest
    except Exception as e:
        logger.warning('[font] 다운로드 실패 (%s): %s', fname, e)
        return None


def _font(bold: bool = False, size: int = 48) -> ImageFont.ImageFont:
    fname = 'NanumGothicBold.ttf' if bold else 'NanumGothic.ttf'
    here  = os.path.dirname(os.path.abspath(__file__))
    root  = os.path.join(here, '..')

    candidates = [
        _ensure_font(fname),                          # static/fonts/ (자동 다운로드)
        os.path.join(root, 'static', 'fonts', fname), # 명시적 경로
        f'C:/Windows/Fonts/{"malgunbd" if bold else "malgun"}.ttf',
        f'/usr/share/fonts/truetype/nanum/{"NanumGothicBold" if bold else "NanumGothic"}.ttf',
        f'/usr/share/fonts/opentype/noto/NotoSansCJK-{"Bold" if bold else "Regular"}.ttc',
        '/System/Library/Fonts/AppleSDGothicNeo.ttc',
    ]
    for p in candidates:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    logger.warning('[font] 한글 폰트를 찾지 못했습니다. 기본 폰트 사용.')
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.ImageFont, max_px: int) -> list[str]:
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


def _draw_text_stroke(d: ImageDraw.ImageDraw, pos: tuple, text: str,
                      font, fill: tuple, stroke_fill: tuple, stroke_w: int = 3):
    """텍스트 스트로크(외곽선) 효과 — 8방향 오프셋으로 선명한 윤곽선 생성."""
    x, y = pos
    for dx in range(-stroke_w, stroke_w + 1):
        for dy in range(-stroke_w, stroke_w + 1):
            if dx == 0 and dy == 0:
                continue
            d.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    d.text((x, y), text, font=font, fill=fill)


def composite_shorts_frame(
    bg_url_or_b64: str,
    overlay_title: str,
    overlay_body: str,
    brand_color: str = '#e8355a',
    pil_size: tuple = (1080, 1920),
) -> str:
    """배경 이미지 + 전문적 텍스트 오버레이 → JPEG base64

    개선사항:
    - 스트로크(외곽선) 효과로 어떤 배경에서도 텍스트 가독성 보장
    - 브랜드 컬러 tint 그라디언트 (순수 검정 대신)
    - 상단 타이틀: 중앙 정렬, 대형 Bold
    - 하단 자막: 반투명 카드 배경 + 브랜드 컬러 바 (40px)
    """
    from services.instagram_service import _load, _jpeg_b64, _hex_rgb

    img = _load(bg_url_or_b64).resize(pil_size, Image.LANCZOS)
    W, H = img.size

    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d  = ImageDraw.Draw(ov)

    br, bg_, bb = _hex_rgb(brand_color)

    # ── 상단 타이틀 배너 (0~22%) ────────────────────────────
    if overlay_title:
        top_h = int(H * 0.22)
        # 브랜드 컬러 tint 그라디언트 (순수 검정보다 세련됨)
        for y in range(0, top_h):
            ratio = 1 - (y / top_h)
            r = int(br * 0.3 * ratio)
            g = int(bg_ * 0.3 * ratio)
            b_c = int(bb * 0.3 * ratio)
            a = int(210 * ratio)
            d.line([(0, y), (W, y)], fill=(r, g, b_c, a))

        tf = _font(bold=True, size=int(H * 0.062))
        lines = _wrap_text(overlay_title, tf, int(W * 0.84))[:2]
        ty = int(H * 0.028)
        for ln in lines:
            bb_box = tf.getbbox(ln)
            lw = bb_box[2] - bb_box[0]
            tx = (W - lw) // 2
            _draw_text_stroke(d, (tx, ty), ln, tf,
                              fill=(255, 255, 255, 255),
                              stroke_fill=(0, 0, 0, 220), stroke_w=4)
            ty += int((bb_box[3] - bb_box[1]) * 1.3)

    # ── 하단 자막 배너 (78%~100%) ───────────────────────────
    if overlay_body:
        bot_start = int(H * 0.78)
        # 브랜드 컬러 tint 그라디언트
        for y in range(bot_start, H):
            ratio = (y - bot_start) / (H - bot_start)
            r = int(max(0, br * 0.25))
            g = int(max(0, bg_ * 0.25))
            b_c = int(max(0, bb * 0.25))
            a = int(230 * ratio)
            d.line([(0, y), (W, y)], fill=(r, g, b_c, a))

        # 하단 자막 카드 배경 (반투명 라운드 느낌)
        card_top = int(H * 0.795)
        card_bot = H - 52
        d.rectangle([(0, card_top), (W, card_bot)], fill=(0, 0, 0, 110))

        # 브랜드 컬러 바 (40px — 존재감 있는 두께)
        d.rectangle([(0, H - 50), (W, H)], fill=(br, bg_, bb, 255))

        bf = _font(bold=True, size=int(H * 0.042))
        max_w = int(W * 0.88)
        lines = _wrap_text(overlay_body, bf, max_w)[:3]
        ty = card_top + int(H * 0.010)
        pad = int(W * 0.055)
        for ln in lines:
            _draw_text_stroke(d, (pad, ty), ln, bf,
                              fill=(255, 255, 255, 255),
                              stroke_fill=(0, 0, 0, 200), stroke_w=3)
            bb_box = bf.getbbox(ln)
            ty += int((bb_box[3] - bb_box[1]) * 1.4)

    combined = Image.alpha_composite(img, ov)
    return _jpeg_b64(combined)


def composite_cta_product_frame(
    product_url: str,
    overlay_title: str,
    overlay_body: str,
    brand_color: str = '#e8355a',
    pil_size: tuple = (1080, 1920),
) -> str:
    """CTA 씬: 브랜드 컬러 배경 위에 제품 이미지 centered fit + 텍스트 오버레이 → JPEG base64"""
    from services.instagram_service import _load, _jpeg_b64, _hex_rgb

    W, H = pil_size
    br, bg_, bb = _hex_rgb(brand_color)

    # 브랜드 컬러 그라디언트 배경
    bg_canvas = Image.new('RGB', (W, H), (max(0, br - 40), max(0, bg_ - 40), max(0, bb - 40)))
    draw_bg = ImageDraw.Draw(bg_canvas)
    for y in range(H):
        ratio = y / H
        r = int(br * (1 - ratio * 0.4))
        g = int(bg_ * (1 - ratio * 0.4))
        b = int(bb * (1 - ratio * 0.4))
        draw_bg.line([(0, y), (W, y)], fill=(r, g, b))

    # 제품 이미지: 중앙 영역(10%~75% 높이)에 비율 유지 fit
    try:
        product_img = _load(product_url).convert('RGBA')
        max_w = int(W * 0.82)
        max_h = int(H * 0.60)
        product_img.thumbnail((max_w, max_h), Image.LANCZOS)
        pw, ph = product_img.size
        px = (W - pw) // 2
        py = int(H * 0.12)
        bg_canvas.paste(product_img, (px, py), product_img if product_img.mode == 'RGBA' else None)
    except Exception as e:
        logger.warning('[cta_frame] 제품 이미지 로드 실패: %s', e)

    img = bg_canvas.convert('RGBA')
    ov  = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d   = ImageDraw.Draw(ov)

    # 상단 타이틀
    if overlay_title:
        tf = _font(bold=True, size=int(H * 0.052))
        lines = _wrap_text(overlay_title, tf, int(W * 0.88))[:2]
        ty = int(H * 0.035)
        for ln in lines:
            bb_box = tf.getbbox(ln)
            lw = bb_box[2] - bb_box[0]
            tx = (W - lw) // 2
            d.text((tx + 2, ty + 2), ln, font=tf, fill=(0, 0, 0, 140))
            d.text((tx, ty), ln, font=tf, fill=(255, 255, 255, 255))
            ty += int((bb_box[3] - bb_box[1]) * 1.35)

    # 하단 CTA 영역 (75%~100%)
    cta_start = int(H * 0.76)
    for y in range(cta_start, H):
        a = int(220 * (y - cta_start) / (H - cta_start))
        d.line([(0, y), (W, y)], fill=(0, 0, 0, a))

    d.rectangle([(0, H - 12), (W, H)], fill=(br, bg_, bb, 255))

    if overlay_body:
        bf = _font(bold=False, size=int(H * 0.038))
        lines = _wrap_text(overlay_body, bf, int(W * 0.88))[:3]
        ty = int(H * 0.78)
        for ln in lines:
            d.text((int(W * 0.06) + 2, ty + 2), ln, font=bf, fill=(0, 0, 0, 150))
            d.text((int(W * 0.06), ty), ln, font=bf, fill=(255, 255, 255, 240))
            bb_box = bf.getbbox(ln)
            ty += int((bb_box[3] - bb_box[1]) * 1.45)

    combined = Image.alpha_composite(img, ov)
    from services.instagram_service import _jpeg_b64
    return _jpeg_b64(combined)


# ════════════════════════════════════════════════════════
# 4. FFmpeg 조립
# ════════════════════════════════════════════════════════

def _ffmpeg(*args: str) -> subprocess.CompletedProcess:
    cmd = ['ffmpeg'] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f'ffmpeg 오류:\n{result.stderr[-2000:]}')
    return result


def _get_audio_duration(mp3_path: str) -> float:
    """ffprobe로 오디오 길이(초) 반환."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_streams', mp3_path],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        for s in data.get('streams', []):
            dur = s.get('duration')
            if dur:
                return float(dur)
    except Exception:
        pass
    return 3.0


BGM_FILES = {
    'lofi':       'bgm_lofi_chill.mp3',
    'upbeat':     'bgm_upbeat_pop.mp3',
    'cinematic':  'bgm_cinematic_calm.mp3',
}


def _get_bgm_path(bgm_key: str) -> str | None:
    """BGM 파일 경로 반환 (없으면 None)."""
    if not bgm_key or bgm_key == 'none':
        return None
    fname = BGM_FILES.get(bgm_key)
    if not fname:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, '..', 'static', 'audio', 'bgm', fname)
    return path if os.path.exists(path) else None


def assemble_shorts_video(
    clip_data: list[dict],  # [{image_path, audio_path}, ...]
    output_path: str,
    bgm_path: str | None = None,
) -> str:
    """이미지+오디오 리스트 → MP4 (1080×1920).

    bgm_path: BGM MP3 경로 (없으면 나레이션만)
    Returns: output_path
    """
    tmp_dir = os.path.dirname(output_path)
    clip_paths = []

    for i, item in enumerate(clip_data):
        img_path   = item['image_path']
        audio_path = item['audio_path']
        clip_out   = os.path.join(tmp_dir, f'clip_{i:02d}.mp4')

        _ffmpeg(
            '-y',
            '-loop', '1', '-i', img_path,
            '-i', audio_path,
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'stillimage',
            '-threads', '2',
            '-c:a', 'aac', '-b:a', '128k',
            '-pix_fmt', 'yuv420p',
            '-shortest',
            clip_out,
        )
        clip_paths.append(clip_out)

    # concat list
    concat_txt = os.path.join(tmp_dir, 'concat.txt')
    with open(concat_txt, 'w') as f:
        for cp in clip_paths:
            f.write(f"file '{cp}'\n")

    concat_out = os.path.join(tmp_dir, 'concat_raw.mp4')
    _ffmpeg(
        '-y',
        '-f', 'concat', '-safe', '0', '-i', concat_txt,
        '-c', 'copy',
        concat_out,
    )

    # BGM 믹스 (파일이 있을 경우)
    if bgm_path:
        # 총 재생시간 파악 (페이드아웃 시작점 계산용)
        total_dur = _get_audio_duration(concat_out) or 20.0
        fade_out_st = max(0, total_dur - 1.0)
        _ffmpeg(
            '-y',
            '-i', concat_out,
            '-stream_loop', '-1', '-i', bgm_path,
            '-filter_complex',
            # 나레이션: 볼륨 유지
            # BGM: 0.13볼륨 + 0.5초 페이드인 + 마지막 1초 페이드아웃
            f'[0:a]volume=1.0[narr];'
            f'[1:a]volume=0.13,afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_st:.2f}:d=1.0[bgm];'
            f'[narr][bgm]amix=inputs=2:duration=first:dropout_transition=0.5[aout]',
            '-map', '0:v', '-map', '[aout]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
            '-shortest',
            output_path,
        )
    else:
        os.rename(concat_out, output_path)

    return output_path


# ════════════════════════════════════════════════════════
# 5. 전체 파이프라인 (백그라운드 스레드)
# ════════════════════════════════════════════════════════

def run_shorts_pipeline(
    creation_id: str,
    user_id: str,
    scenes: list[dict],
    style: str,
    brand_color: str,
    voice_key: str,
    tts_speed: float,
    supabase,
    product_image_url: str = '',
    visual_mode: str = 'scene_mood',
    bgm_key: str = 'none',
) -> None:
    """백그라운드 스레드에서 실행. Supabase creation 상태 업데이트."""
    tmp_dir = os.path.join(tempfile.gettempdir(), f'maesil_shorts_{creation_id}')
    os.makedirs(tmp_dir, exist_ok=True)

    def _update(status: str, extra: dict | None = None):
        row = {'status': status}
        if extra:
            row['output_data'] = extra
        try:
            supabase.table('creations').update(row).eq('id', creation_id).execute()
        except Exception as e:
            logger.error('[shorts] supabase update error: %s', e)

    try:
        from services.config_service import get_config
        from services.imagen_service import _generate_flux, upload_to_supabase

        tts_api_key = get_config('google_tts_api_key')
        if not tts_api_key:
            raise ValueError('google_tts_api_key가 설정되지 않았습니다. 시스템 설정에서 등록하세요.')

        clip_data = []
        pil_size  = (1080, 1920)
        style_mod = SHORTS_STYLE_PRESETS.get(style, '')

        for i, scene in enumerate(scenes):
            step = f'씬 {i+1}/{len(scenes)} 생성 중'
            _update('generating', {'progress': i, 'step': step})

            role = scene.get('role', '')
            is_cta = (role == 'cta') or (i == len(scenes) - 1)

            # ── 이미지 생성 ──────────────────────────────────
            if is_cta and product_image_url:
                # CTA 씬: 실제 제품 이미지 사용
                frame_b64 = composite_cta_product_frame(
                    product_image_url,
                    scene.get('overlay_title', ''),
                    scene.get('overlay_body', scene.get('narration', '')),
                    brand_color,
                    pil_size,
                )
            else:
                # FLUX 이미지 생성 (Schnell + 퀄리티 최적화 프롬프트)
                flux_p = scene.get('flux_prompt', '')
                if style_mod:
                    flux_p = f'{flux_p}, {style_mod}'
                flux_p += _QUALITY_SUFFIX
                flux_p += _NO_TEXT
                # 사람이 등장할 가능성이 있는 씬에 해부학 네거티브 추가
                if any(kw in flux_p.lower() for kw in ['person', 'woman', 'man', 'hand', 'people', 'human', 'mother', 'baby', 'child', 'girl', 'boy']):
                    flux_p += _NO_ANATOMY

                img_url, _ = _generate_flux(flux_p, 'flux_preview', '1080x1920')

                frame_b64 = composite_shorts_frame(
                    img_url,
                    scene.get('overlay_title', ''),
                    scene.get('overlay_body', scene.get('narration', '')),
                    brand_color,
                    pil_size,
                )

            # 이미지 저장
            img_path = os.path.join(tmp_dir, f'scene_{i:02d}.jpg')
            _, b64data = frame_b64.split(',', 1)
            with open(img_path, 'wb') as f:
                f.write(base64.b64decode(b64data))

            # TTS (씬 역할별 피치·속도 자동 적용)
            narration  = _normalize_tts_text(scene.get('narration', ''))
            scene_role = scene.get('role', '')
            mp3_bytes  = tts_synthesize(narration, tts_api_key, voice_key, tts_speed, scene_role)
            audio_path = os.path.join(tmp_dir, f'scene_{i:02d}.mp3')
            with open(audio_path, 'wb') as f:
                f.write(mp3_bytes)

            clip_data.append({'image_path': img_path, 'audio_path': audio_path})

        # FFmpeg 조립
        _update('generating', {'progress': len(scenes), 'step': 'FFmpeg 영상 조립 중'})
        output_mp4 = os.path.join(tmp_dir, 'shorts.mp4')
        bgm_path = _get_bgm_path(bgm_key)
        assemble_shorts_video(clip_data, output_mp4, bgm_path=bgm_path)

        # Supabase Storage 업로드
        _update('generating', {'progress': len(scenes) + 1, 'step': '업로드 중'})
        with open(output_mp4, 'rb') as f:
            video_bytes = f.read()

        path = f'{user_id}/{uuid.uuid4().hex}_shorts.mp4'
        supabase.storage.from_('creations').upload(
            path, video_bytes, {'content-type': 'video/mp4'}
        )
        video_url = supabase.storage.from_('creations').get_public_url(path)

        _update('done', {'video_url': video_url, 'progress': len(scenes) + 2})
        logger.info('[shorts] 완료: %s → %s', creation_id, video_url)

    except Exception as e:
        logger.error('[shorts] 파이프라인 오류 (%s): %s', creation_id, e)
        _update('failed', {'error': str(e)})
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        # 24시간 이상 된 고아 tmp 디렉토리 정리
        _cleanup_stale_tmp_dirs()


def _cleanup_stale_tmp_dirs(max_age_hours: int = 24) -> None:
    """24시간 이상 된 maesil_shorts_* tmp 디렉토리 정리."""
    try:
        pattern = os.path.join(tempfile.gettempdir(), 'maesil_shorts_*')
        cutoff = time.time() - max_age_hours * 3600
        for d in _glob.glob(pattern):
            if os.path.isdir(d) and os.path.getmtime(d) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def start_shorts_pipeline(
    creation_id: str,
    user_id: str,
    scenes: list[dict],
    style: str,
    brand_color: str,
    voice_key: str,
    tts_speed: float,
    supabase,
    app=None,
    product_image_url: str = '',
    visual_mode: str = 'scene_mood',
    bgm_key: str = 'none',
) -> None:
    """REDIS_URL이 설정된 경우 Celery 워커로 오프로드, 없으면 daemon thread 사용."""
    import os

    redis_url = os.environ.get('REDIS_URL', '')
    if redis_url:
        # Celery 워커로 오프로드 — Flask 앱과 별도 프로세스에서 실행
        try:
            from tasks.shorts_task import generate_shorts_video
            supabase_url = os.environ.get('SUPABASE_URL', '')
            supabase_key = os.environ.get('SUPABASE_SERVICE_KEY', '')
            generate_shorts_video.delay(
                creation_id=creation_id,
                user_id=user_id,
                scenes=scenes,
                style=style,
                brand_color=brand_color,
                voice_key=voice_key,
                tts_speed=tts_speed,
                supabase_url=supabase_url,
                supabase_key=supabase_key,
                product_image_url=product_image_url,
                visual_mode=visual_mode,
                bgm_key=bgm_key,
            )
            logger.info('[shorts] Celery 워커로 오프로드: %s', creation_id)
            return
        except Exception as e:
            logger.warning('[shorts] Celery 오프로드 실패, thread로 폴백: %s', e)

    # 폴백: daemon thread (Redis 없는 개발 환경)
    def _run():
        kwargs = dict(
            creation_id=creation_id, user_id=user_id, scenes=scenes,
            style=style, brand_color=brand_color, voice_key=voice_key,
            tts_speed=tts_speed, supabase=supabase,
            product_image_url=product_image_url,
            visual_mode=visual_mode, bgm_key=bgm_key,
        )
        if app:
            with app.app_context():
                run_shorts_pipeline(**kwargs)
        else:
            run_shorts_pipeline(**kwargs)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
