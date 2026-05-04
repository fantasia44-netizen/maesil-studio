"""쇼츠/릴스 영상 자동 생성 파이프라인

구조: 훅 → 공감 → 해결 → 핵심혜택 → CTA  (5씬, 7~20초)
엔진: FLUX Schnell(이미지) + Google TTS(나레이션) + FFmpeg(조립)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
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
    'realistic_banner': 'cinematic lifestyle photography, vertical 9:16 frame, warm bokeh, Korean aesthetic',
    'webtoon':          'Korean webtoon illustration, clean line art, vibrant colors, vertical composition',
    'ghibli':           'Studio Ghibli watercolor style, soft pastel, whimsical natural background, 9:16',
    'flat_modern':      'modern flat illustration, bold color blocks, editorial vector art, vertical frame',
    'disney':           'Pixar/Disney 3D render style, warm cinematic lighting, expressive, vertical 9:16',
}

_NO_CJK = (
    ', no Chinese characters, no Japanese characters, no kanji, no hanzi, '
    'no CJK text, Latin alphabet only for any visible text'
)


# ════════════════════════════════════════════════════════
# 1. 대본 생성
# ════════════════════════════════════════════════════════

def generate_shorts_script(brand_ctx: str, angle: dict, style: str = 'realistic_banner') -> list[dict]:
    """Claude로 5씬 쇼츠 대본 생성.

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

    system = '당신은 숏폼 영상 전문 크리에이터입니다. 순수 JSON만 출력하세요.'
    prompt = f"""인스타 릴스/유튜브 쇼츠용 5씬 대본을 JSON으로 생성하세요.

[브랜드·상품]
{brand_ctx}

[소구포인트]
- 방향: {angle_title}
- 이미지 분위기: {angle_vibe}
- 후킹 문구: {angle_hook}

[씬 구조 — 총 7~20초]
{scenes_desc}

[이미지 스타일]
{style_guide}

[출력 형식 — 순수 JSON 배열]
[
  {{
    "role": "hook",
    "narration": "나레이션 텍스트 (한글, 2~4초 분량, 10~25자)",
    "overlay_title": "화면 상단 굵은 텍스트 (한글, 12자 이내)",
    "overlay_body": "화면 하단 자막 텍스트 (한글, narration과 동일 또는 축약)",
    "flux_prompt": "영문 FLUX 이미지 프롬프트 (50~70단어, 9:16 vertical, 피사체·조명·분위기 구체적으로. 한글 텍스트·글자 절대 포함 금지)"
  }},
  ...5개 씬 모두...
]

규칙:
- narration은 실제 TTS로 읽힐 텍스트이므로 자연스러운 구어체로
- overlay_title은 임팩트 있는 키워드 (예: "지금 바꿔야 할 이유")
- flux_prompt에 한글/중국어/일본어 절대 금지
- 순수 JSON 배열만 출력"""

    raw = generate_text(system, prompt, max_tokens=1200, model='claude-haiku-4-5-20251001')
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
    'female_natural': ('ko-KR', 'ko-KR-Neural2-A', 'Neural2'),
    'male_calm':      ('ko-KR', 'ko-KR-Neural2-C', 'Neural2'),
    'female_bright':  ('ko-KR', 'ko-KR-Neural2-B', 'Neural2'),
    'male_clear':     ('ko-KR', 'ko-KR-Wavenet-C', 'Wavenet'),
}


def tts_synthesize(text: str, api_key: str,
                   voice_key: str = 'female_natural',
                   speed: float = 1.1) -> bytes:
    """Google TTS REST API → MP3 bytes."""
    lang, name, _ = VOICE_OPTIONS.get(voice_key, VOICE_OPTIONS['female_natural'])
    resp = requests.post(
        f'https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}',
        json={
            'input': {'text': text},
            'voice': {'languageCode': lang, 'name': name},
            'audioConfig': {
                'audioEncoding': 'MP3',
                'speakingRate': speed,
                'pitch': 0.0,
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

def _font(bold: bool = False, size: int = 48) -> ImageFont.ImageFont:
    here  = os.path.dirname(os.path.abspath(__file__))
    root  = os.path.join(here, '..')
    fname = 'NanumGothicBold.ttf' if bold else 'NanumGothic.ttf'
    candidates = [
        os.path.join(root, 'static', 'fonts', fname),
        f'C:/Windows/Fonts/{"malgunbd" if bold else "malgun"}.ttf',
        f'/usr/share/fonts/truetype/nanum/{"NanumGothicBold" if bold else "NanumGothic"}.ttf',
        f'/usr/share/fonts/opentype/noto/NotoSansCJK-{"Bold" if bold else "Regular"}.ttc',
        '/System/Library/Fonts/AppleSDGothicNeo.ttc',
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
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


def composite_shorts_frame(
    bg_url_or_b64: str,
    overlay_title: str,
    overlay_body: str,
    brand_color: str = '#e8355a',
    pil_size: tuple = (1080, 1920),
) -> str:
    """배경 이미지 + 상단 제목 + 하단 자막 → JPEG base64"""
    from services.instagram_service import _load, _jpeg_b64, _hex_rgb

    img = _load(bg_url_or_b64).resize(pil_size, Image.LANCZOS)
    W, H = img.size

    ov  = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d   = ImageDraw.Draw(ov)

    br, bg_, bb = _hex_rgb(brand_color)

    # ── 상단 타이틀 배너 (0~18%) ────────────────────────
    if overlay_title:
        top_h = int(H * 0.18)
        for y in range(0, top_h):
            a = int(200 * (1 - y / top_h))
            d.line([(0, y), (W, y)], fill=(8, 8, 8, a))

        tf = _font(bold=True, size=int(H * 0.055))
        lines = _wrap_text(overlay_title, tf, int(W * 0.88))[:2]
        ty = int(H * 0.03)
        for ln in lines:
            bb_box = tf.getbbox(ln)
            lw = bb_box[2] - bb_box[0]
            tx = (W - lw) // 2
            d.text((tx + 2, ty + 2), ln, font=tf, fill=(0, 0, 0, 160))
            d.text((tx,     ty    ), ln, font=tf, fill=(255, 255, 255, 255))
            ty += int((bb_box[3] - bb_box[1]) * 1.35)

    # ── 하단 자막 배너 (82%~100%) ───────────────────────
    if overlay_body:
        bot_start = int(H * 0.82)
        for y in range(bot_start, H):
            a = int(210 * (y - bot_start) / (H - bot_start))
            d.line([(0, y), (W, y)], fill=(8, 8, 8, a))

        # 브랜드 컬러 바 (맨 아래 10px)
        d.rectangle([(0, H - 10), (W, H)], fill=(br, bg_, bb, 255))

        bf  = _font(bold=False, size=int(H * 0.038))
        max_w = int(W * 0.88)
        lines = _wrap_text(overlay_body, bf, max_w)[:3]
        ty = int(H * 0.836)
        for ln in lines:
            d.text((int(W * 0.06) + 2, ty + 2), ln, font=bf, fill=(0, 0, 0, 150))
            d.text((int(W * 0.06),     ty    ), ln, font=bf, fill=(255, 255, 255, 240))
            bb_box = bf.getbbox(ln)
            ty += int((bb_box[3] - bb_box[1]) * 1.45)

    combined = Image.alpha_composite(img, ov)
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


def assemble_shorts_video(
    clip_data: list[dict],  # [{image_path, audio_path}, ...]
    output_path: str,
) -> str:
    """이미지+오디오 리스트 → MP4 (1080×1920).

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

    _ffmpeg(
        '-y',
        '-f', 'concat', '-safe', '0', '-i', concat_txt,
        '-c', 'copy',
        output_path,
    )
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

        for i, scene in enumerate(scenes):
            step = f'씬 {i+1}/{len(scenes)} 생성 중'
            _update('generating', {'progress': i, 'step': step})

            # 이미지 생성
            style_mod = SHORTS_STYLE_PRESETS.get(style, '')
            flux_p = scene.get('flux_prompt', '') + (f', {style_mod}' if style_mod else '') + _NO_CJK
            img_url, _ = _generate_flux(flux_p, 'flux_preview', '1080x1920')

            # PIL 오버레이
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

            # TTS
            narration = scene.get('narration', '')
            mp3_bytes = tts_synthesize(narration, tts_api_key, voice_key, tts_speed)
            audio_path = os.path.join(tmp_dir, f'scene_{i:02d}.mp3')
            with open(audio_path, 'wb') as f:
                f.write(mp3_bytes)

            clip_data.append({'image_path': img_path, 'audio_path': audio_path})

        # FFmpeg 조립
        _update('generating', {'progress': len(scenes), 'step': 'FFmpeg 영상 조립 중'})
        output_mp4 = os.path.join(tmp_dir, 'shorts.mp4')
        assemble_shorts_video(clip_data, output_mp4)

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
        # 임시 파일 정리
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
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
) -> None:
    def _run():
        if app:
            with app.app_context():
                run_shorts_pipeline(
                    creation_id=creation_id, user_id=user_id, scenes=scenes,
                    style=style, brand_color=brand_color, voice_key=voice_key,
                    tts_speed=tts_speed, supabase=supabase,
                )
        else:
            run_shorts_pipeline(
                creation_id=creation_id, user_id=user_id, scenes=scenes,
                style=style, brand_color=brand_color, voice_key=voice_key,
                tts_speed=tts_speed, supabase=supabase,
            )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
