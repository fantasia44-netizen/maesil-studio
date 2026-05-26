"""쇼츠/릴스 영상 자동 생성 파이프라인

구조: 훅 → 공감 → 해결 → 핵심혜택 → CTA  (5씬, 7~20초)
엔진: FLUX Schnell(이미지) + Google TTS(나레이션) + FFmpeg(조립) + Suno BGM(배경음)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import time
import threading
import uuid
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── FFmpeg 프로세스 추적 레지스트리 (좀비 방지) ──────────────────
_tracked_procs: set = set()
_tracked_procs_lock = threading.Lock()


def _register_proc(proc) -> None:
    """실행 중인 FFmpeg 프로세스 등록."""
    with _tracked_procs_lock:
        _tracked_procs.add(proc)


def _unregister_proc(proc) -> None:
    """FFmpeg 프로세스 추적 해제."""
    with _tracked_procs_lock:
        _tracked_procs.discard(proc)


def _kill_all_tracked_procs() -> None:
    """워커 종료 시 추적 중인 모든 FFmpeg 프로세스 그룹 강제 종료."""
    import signal as _signal
    with _tracked_procs_lock:
        procs = list(_tracked_procs)
    for proc in procs:
        try:
            if hasattr(os, 'killpg'):  # POSIX(Linux) only
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, _signal.SIGKILL)
            else:
                proc.kill()  # Windows fallback
            logger.info('[ffmpeg] 좀비 방지 — PID %d 프로세스 그룹 종료', proc.pid)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

# ── 씬 역할 정의 ─────────────────────────────────────────────
SCENE_ROLES = [
    ('hook',     '훅',       '시청자 시선 즉시 포착 — 충격적 질문/수치/반전 (2~3초)'),
    ('empathy',  '공감',     '타겟의 문제/불편함 공감 — "이런 경험 있으신가요?" (3~4초)'),
    ('solution', '해결',     '제품/서비스가 어떻게 해결하는지 (3~4초)'),
    ('benefit',  '핵심혜택', '가장 강력한 한 가지 혜택/차별점 (3~4초)'),
    ('cta',      'CTA',      '구체적 행동 유도 — 링크/댓글/팔로우 (2~3초). 이미지는 손·손가락 없는 제품/화면 구도'),
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
    ', no text, no letters, no words, no signs, no watermarks'
    ', no Chinese characters, no Japanese characters, no Korean characters'
    ', no kanji, no hanzi, no hangul, no CJK glyphs'
    ', absolutely no writing of any kind on any surface'
)
_NO_ANATOMY = (
    ', perfect hands, anatomically correct hands, correct finger count'
    ', five fingers per hand, realistic fingers, no extra fingers'
    ', no missing fingers, no fused fingers, no malformed hands'
    ', no extra limbs, no extra arms, no extra legs'
    ', no duplicate body parts, anatomically correct body'
)


# ════════════════════════════════════════════════════════
# BGM 분위기 정의 (Suno 생성 폴더 구조)
# static/sounds/bgm/{mood_key}/ 에 MP3 파일 보관
# ════════════════════════════════════════════════════════

BGM_MOODS: dict[str, dict] = {
    'energetic': {
        'label': '에너지틱',
        'desc': '역동적·트렌디 — 스포츠·건강기능식품·다이어트',
        'suno_prompt': (
            'upbeat energetic pop electronic, driving synth bass, '
            'punchy drums, motivational vibe, no lyrics, instrumental, '
            '120-128 BPM, Korean commercial ad style, 30 seconds'
        ),
    },
    'emotional': {
        'label': '감성적',
        'desc': '따뜻하고 공감되는 — 화장품·라이프스타일·육아',
        'suno_prompt': (
            'warm emotional acoustic pop, soft piano melody, '
            'gentle strings, heartfelt mood, no lyrics, instrumental, '
            '80-90 BPM, cinematic warmth, 30 seconds'
        ),
    },
    'upbeat_pop': {
        'label': '경쾌한 팝',
        'desc': '밝고 신나는 — 식품·음료·일상 소비재',
        'suno_prompt': (
            'bright cheerful K-pop style, light guitar strum, '
            'happy whistling, summer vibes, no lyrics, instrumental, '
            '100-110 BPM, fresh and fun, 30 seconds'
        ),
    },
    'luxury': {
        'label': '고급스러운',
        'desc': '우아하고 세련된 — 럭셔리 뷰티·패션·프리미엄',
        'suno_prompt': (
            'elegant cinematic luxury, soft jazz piano, '
            'subtle orchestral sweep, sophisticated mood, no lyrics, '
            'instrumental, 70-80 BPM, premium brand feel, 30 seconds'
        ),
    },
    'playful': {
        'label': '귀엽고 재미있는',
        'desc': '캐릭터·어린이·캐주얼 앱·게임',
        'suno_prompt': (
            'cute playful cartoon style, xylophone melody, '
            'bouncy ukulele, fun quirky sound effects, no lyrics, '
            'instrumental, 115-125 BPM, light and whimsical, 30 seconds'
        ),
    },
    'dramatic': {
        'label': '드라마틱',
        'desc': '강렬한 훅·문제 제시 — 보험·법률·솔루션 서비스',
        'suno_prompt': (
            'dramatic cinematic tension, pulsing low synth, '
            'building percussion, suspenseful mood, no lyrics, '
            'instrumental, 90-100 BPM, problem-aware tone, 30 seconds'
        ),
    },
    'calm_ambient': {
        'label': '차분한 앰비언트',
        'desc': '힐링·웰니스·명상·수면',
        'suno_prompt': (
            'calm lo-fi ambient, soft pad chords, '
            'gentle nature sounds, relaxing meditation vibe, no lyrics, '
            'instrumental, 60-70 BPM, peaceful and serene, 30 seconds'
        ),
    },
    'trendy_hiphop': {
        'label': '트렌디 힙합',
        'desc': '패션·뷰티·MZ세대 타겟',
        'suno_prompt': (
            'trendy lo-fi hip hop beat, warm vinyl crackle, '
            'chill boom-bap, modern Korean street vibe, no lyrics, '
            'instrumental, 85-95 BPM, cool and stylish, 30 seconds'
        ),
    },
    'inspiring': {
        'label': '영감·동기부여',
        'desc': '교육·비즈니스·자기계발·SaaS',
        'suno_prompt': (
            'inspiring uplifting corporate, light piano arpeggios, '
            'rising strings, motivational crescendo, no lyrics, '
            'instrumental, 95-105 BPM, forward momentum, 30 seconds'
        ),
    },
    'korean_vibe': {
        'label': '한국 감성',
        'desc': 'K-뷰티·전통·한식·국내 정서',
        'suno_prompt': (
            'modern K-indie acoustic, soft gayageum-inspired melody, '
            'gentle acoustic guitar, nostalgic Korean sentiment, no lyrics, '
            'instrumental, 80-90 BPM, warm and familiar, 30 seconds'
        ),
    },
}

# 이미지 스타일 → BGM 분위기 매핑
STYLE_TO_MOOD: dict[str, list[str]] = {
    'realistic_banner': ['emotional', 'inspiring', 'luxury'],
    'webtoon':          ['playful',   'upbeat_pop', 'trendy_hiphop'],
    'ghibli':           ['calm_ambient', 'emotional', 'korean_vibe'],
    'flat_modern':      ['trendy_hiphop', 'energetic', 'upbeat_pop'],
    'disney':           ['playful',   'upbeat_pop', 'inspiring'],
}

_BGM_ROOT: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'static', 'sounds', 'bgm'
)


def _list_bgm_files(mood: str | None = None) -> list[str]:
    """지정 mood 폴더(또는 전체)에서 MP3 파일 목록 반환."""
    root = os.path.normpath(_BGM_ROOT)
    if mood:
        folder = os.path.join(root, mood)
        if os.path.isdir(folder):
            return [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.lower().endswith(('.mp3', '.wav', '.ogg'))
            ]
    # 전체 탐색 (하위 폴더 포함)
    result = []
    if not os.path.isdir(root):
        return result
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(('.mp3', '.wav', '.ogg')):
                result.append(os.path.join(dirpath, f))
    return result


def pick_bgm(style: str | None = None) -> str | None:
    """스타일에 맞는 BGM 파일 경로 랜덤 반환. 없으면 None."""
    candidates: list[str] = []

    # 1) 스타일 → mood 우선 탐색
    if style and style in STYLE_TO_MOOD:
        for mood in STYLE_TO_MOOD[style]:
            candidates = _list_bgm_files(mood)
            if candidates:
                break

    # 2) mood 폴더에 파일 없으면 전체 탐색
    if not candidates:
        candidates = _list_bgm_files()

    if not candidates:
        return None
    return random.choice(candidates)


def mix_bgm_into_video(
    raw_mp4: str,
    bgm_path: str,
    output_mp4: str,
    volume: float = 0.20,
) -> str:
    """TTS가 포함된 영상에 BGM을 낮은 볼륨으로 믹싱해 새 MP4 반환.

    - BGM은 영상 길이에 맞춰 자동 루프/트림
    - volume: 0.0 ~ 1.0 (기본 0.20 = 20%)
    """
    vol = max(0.01, min(1.0, volume))
    # amix duration=first → TTS 음성 끝나면 BGM도 종료
    filter_str = (
        f'[1:a]volume={vol:.2f},aloop=loop=-1:size=2147483647[bgm];'
        f'[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]'
    )
    cmd = [
        'ffmpeg', '-y',
        '-i', raw_mp4,
        '-stream_loop', '-1', '-i', bgm_path,
        '-filter_complex', filter_str,
        '-map', '0:v',
        '-map', '[aout]',
        '-c:v', 'copy',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest',
        output_mp4,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f'BGM 믹싱 오류:\n{result.stderr[-1500:]}')
    return output_mp4


# ════════════════════════════════════════════════════════
# 1. 대본 생성
# ════════════════════════════════════════════════════════

def generate_shorts_script(
    brand_ctx: str,
    angle: dict,
    style: str = 'realistic_banner',
    reveal_mode: bool = False,
) -> list[dict]:
    """Claude로 쇼츠 대본 생성.

    Args:
        reveal_mode: True = 제품 리빌 모드 (3씬, PAS 구조)
                     - 씬1·2: 공감·문제 (제품 미등장, 순수 상황 묘사)
                     - 씬3: 제품 등장 (해결 리빌)
                     False = 일반 5씬 모드

    Returns: [{role, role_ko, narration, overlay_title, overlay_body, flux_prompt}, ...]
    """
    from services.claude_service import generate_text

    angle_title    = angle.get('title',    '') if isinstance(angle, dict) else ''
    angle_vibe     = angle.get('image_vibe','') if isinstance(angle, dict) else ''
    angle_hook     = angle.get('hook',     '') if isinstance(angle, dict) else ''
    angle_problem  = angle.get('problem',  '') if isinstance(angle, dict) else ''
    angle_solution = angle.get('solution', '') if isinstance(angle, dict) else ''
    angle_result   = angle.get('result',   '') if isinstance(angle, dict) else ''
    style_guide    = SHORTS_STYLE_PRESETS.get(style, SHORTS_STYLE_PRESETS['realistic_banner'])

    # ── 제품 리빌 모드 (3씬 PAS) ────────────────────────────────
    if reveal_mode:
        system = (
            '당신은 퍼포먼스 마케터이자 숏폼 영상 전문 크리에이터입니다. '
            '효과적인 광고는 제품 소개로 시작하지 않습니다. '
            '먼저 타겟의 고통에 공감시키고, 궁금증이 극에 달했을 때 제품을 등장시킵니다. '
            '이것이 이탈률을 낮추고 전환율을 높이는 핵심입니다. '
            '순수 JSON만 출력하세요.'
        )
        prompt = f"""인스타 릴스/쇼츠용 3씬 광고 대본 (제품 리빌 구조 — PAS 공식).

[브랜드·상품 정보 — 씬1·2에서는 제품명/브랜드명 절대 언급 금지]
{brand_ctx}

[소구포인트]
- 타겟의 문제: {angle_problem}
- 후킹 문구: {angle_hook}
- 해결 방식: {angle_solution}
- 변화/결과: {angle_result}
- 분위기: {angle_vibe}

[3씬 구조 — 철저한 PAS 공식]
씬1 (Hook/Problem): 타겟이 겪는 구체적 문제·불편을 생생하게 묘사. 제품 미등장.
  flux_prompt: 문제 상황을 겪는 사람의 일상 장면. 제품·브랜드 없음. 공감 가는 리얼 상황.

씬2 (Agitate/Empathy): 그 불편이 얼마나 반복되는지 심화. 공감+궁금증 자극. 제품 미등장.
  flux_prompt: 문제로 인한 감정(피로·포기·답답함)이 담긴 장면. 제품 없음.

씬3 (Solution/Reveal): 제품이 처음으로 등장. "바로 이거였어요" 톤. 결과·변화 제시.
  flux_prompt: 제품이 주인공. 글래머 조명, 프리미엄 배경, 제품의 특징이 돋보이는 구도.
  ⚠️ 이 씬의 flux_prompt는 제품 이미지가 대체하므로 배경/분위기 묘사 위주로 작성.

[이미지 스타일]
{style_guide}

[출력 — 순수 JSON 배열, 정확히 3개]
[
※ narration(TTS 낭독용)과 overlay_body(화면 자막용)는 반드시 다른 표현으로 작성하세요.
  {{
    "role": "hook",
    "narration": "🎙 TTS용 — 귀로 들을 때 자연스러운 구어체 완성 문장. 20~40자. 예: '광고비는 계속 나가는데 매출은 왜 이럴까요?'",
    "overlay_title": "👁 스크롤 멈출 임팩트 텍스트. 10자 이내. 예: '이거 내 얘기', '실화냐'",
    "overlay_body": "👁 화면 자막 — 15자 이내, narration과 다른 짧은 표현. 예: '광고비만 줄줄 새고'",
    "flux_prompt": "영문 전용, 문제 상황 장면 묘사, 60~80단어, 9:16 vertical, no product, no text, no CJK"
  }},
  {{
    "role": "empathy",
    "narration": "🎙 TTS용 — 고백하듯 공감을 심화하는 구어체 완성 문장. 20~40자. 예: '솔직히 이런 상황에서 뭘 어떻게 해야 할지 막막하잖아요'",
    "overlay_title": "👁 공감 키워드. 10자 이내. 예: '나만 이런 거 아니었어'",
    "overlay_body": "👁 화면 자막 — 15자 이내, 공감 핵심. 예: '나도 그랬어요'",
    "flux_prompt": "영문 전용, 감정/피로/답답함 장면, 60~80단어, 9:16 vertical, no product, no text, no CJK"
  }},
  {{
    "role": "solution",
    "narration": "🎙 TTS용 — 제품 등장, 변화 전달하는 구어체 완성 문장. 20~45자. 예: '이걸 쓰고 나서 처음으로 광고비가 아깝지 않았어요'",
    "overlay_title": "👁 임팩트 해결 문구. 10자 이내. 예: '진짜 달라졌다'",
    "overlay_body": "👁 화면 자막 — 15자 이내, CTA 포함. 예: '지금 링크에서 확인하세요'",
    "flux_prompt": "영문 전용, 프리미엄 제품 배경·분위기 묘사 (제품 이미지로 대체됨), glamour studio lighting, 60~80단어, 9:16 vertical, no text, no CJK"
  }}
]

순수 JSON 배열만 출력. 씬1·2 naration과 flux_prompt에 제품명·브랜드명 절대 포함 금지."""

        raw   = generate_text(system, prompt, max_tokens=900, model='claude-sonnet-4-6')
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        s, e  = clean.find('['), clean.rfind(']') + 1
        if s >= 0 and e > s:
            clean = clean[s:e]
        scenes = json.loads(clean)

        reveal_roles = [('hook', '훅'), ('empathy', '공감'), ('solution', '제품 리빌')]
        for i, sc in enumerate(scenes[:3]):
            sc['role']        = reveal_roles[i][0]
            sc['role_ko']     = reveal_roles[i][1]
            sc['reveal_mode'] = True
            sc['is_product_scene'] = (i == 2)  # 마지막 씬 = 제품 등장
        return scenes[:3]

    # ── 일반 5씬 모드 ────────────────────────────────────────────
    scenes_desc = '\n'.join(
        f'- scene {i+1} "{r[1]}" ({r[0]}): {r[2]}'
        for i, r in enumerate(SCENE_ROLES)
    )

    system = (
        '당신은 MZ세대가 열광하는 숏폼 크리에이터입니다. '
        '좋은 쇼츠 광고의 핵심 원칙 4가지를 반드시 지키세요:\n'
        '1. 기능 나열 절대 금지 — "효과적입니다, 편리합니다, 우수합니다" 같은 말은 공감을 죽입니다\n'
        '2. 감성 장면으로 스토리 구성 — 타겟이 겪는 상황을 눈에 보이는 생생한 장면으로 그리세요\n'
        '3. 해결은 감정 변화로 전달 — "이걸 쓰고 처음으로 여유 있는 아침을 맞았어요" 형태\n'
        '4. 다음 씬이 궁금하게 — 각 씬 끝에 궁금증이나 기대감을 남기세요\n'
        '나레이션은 친구에게 말하듯 구어체로, TTS로 읽히는 텍스트입니다. '
        'overlay_title은 10자 이내로 짧고 강렬하게, 시청자가 즉시 공감할 구어체 표현입니다. '
        'narration은 맞춤법과 띄어쓰기를 정확히 지켜서 TTS가 자연스럽게 읽히도록 합니다. '
        '순수 JSON만 출력하세요.'
    )
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

[출력 형식 — 순수 JSON 배열, 5개 씬]
※ narration(TTS용)과 overlay_body(자막용)는 목적이 다릅니다. 반드시 다르게 작성하세요.
[
  {{
    "role": "hook",
    "narration": "🎙 TTS 낭독용 — 귀로 들을 때 자연스러운 구어체. 2~4초 분량(20~45자). 말하는 것처럼 완성된 문장으로. 맞춤법·띄어쓰기 정확히. 예: '광고비는 계속 나가는데 매출은 제자리인 적 없으세요?'",
    "overlay_title": "👁 화면 상단 — 스크롤 멈추게 할 임팩트 텍스트. 10자 이내, 구어체. 예: '이거 내 얘기', '실화냐', '솔직히 나도', '진짜 달라짐'",
    "overlay_body": "👁 화면 하단 자막 — 눈으로 읽는 텍스트. 15자 이내, narration과 다른 표현으로 핵심만. 예: '광고비만 새고 수익은 제자리', '이거 하나로 바뀌었어요'",
    "flux_prompt": "FLUX 이미지 프롬프트 — 반드시 영문(English)만, 60~80단어, 9:16 vertical frame. 씬 내용과 분위기에 맞는 피사체·조명·배경을 구체적으로 묘사. 글자·텍스트·CJK 문자 절대 포함 금지"
  }},
  ...5개 씬...
]

씬별 감성 작성 가이드:
- hook: 타겟이 겪는 상황을 영화 한 장면처럼 생생하게 → 1초 안에 "맞아, 나 이거야" 반응
  narration 예시: "광고비는 계속 나가는데 매출은 왜 이럴까요?"
  overlay 예시: "광고비만 줄줄 새고"
- empathy: 그 감정을 더 깊게 파고들기 — 고백하듯, 나도 겪어봤다는 톤
  narration 예시: "솔직히 이런 상황에서 뭘 어떻게 해야 할지 막막하잖아요"
  overlay 예시: "나만 이런 거 아니었어"
- solution: 제품 기능 설명 절대 금지. 그 제품을 만난 순간의 감정 변화를 묘사
  narration 예시: "그때 이걸 처음 써봤는데 진짜 달라지더라고요"
  overlay 예시: "처음으로 숫자가 달라졌다"
- benefit: 구체적인 삶의 변화를 한 장면으로 — 숫자보다 이야기
  narration 예시: "이제는 광고비 쓰는 게 아깝지 않아요. 쓸 곳에만 쓰니까요"
  overlay 예시: "쓸 곳에만 쓰는 광고"
- cta: 공감한 사람들에게 손 내밀기 — "당신도 이런 경험 있다면"
  narration 예시: "저처럼 광고비 고민하셨다면, 한번 확인해보세요"
  overlay 예시: "링크에서 확인하세요"
  flux_prompt 주의: 손·손가락이 나오는 구도(폰 들기, 손가락으로 가리키기 등) 절대 금지.
  대신 제품/앱 화면을 테이블 위에 놓거나, 브랜드 컬러 배경에 스마트폰 정면, 밝고 깔끔한 스튜디오 느낌으로.

순수 JSON 배열만 출력"""

    raw = generate_text(system, prompt, max_tokens=1500, model='claude-sonnet-4-6')
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


def _draw_text_stroke(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple = (255, 255, 255, 255),
    stroke_fill: tuple = (0, 0, 0, 220),
    stroke_w: int = 3,
) -> None:
    """텍스트 외곽선(stroke) 포함 그리기 헬퍼.

    PIL ImageDraw.text의 stroke_width/stroke_fill 파라미터를 래핑.
    """
    x, y = xy
    draw.text((x, y), text, font=font, fill=fill,
              stroke_width=stroke_w, stroke_fill=stroke_fill)


def _wrap_text(text: str, font: ImageFont.ImageFont, max_px: int) -> list[str]:
    """단어(공백) 경계 우선 줄바꿈 + widow(홀로 남는 단어) 방지."""
    def _px(s: str) -> int:
        bb = font.getbbox(s)
        return bb[2] - bb[0]

    words = text.split(' ')

    # ── 1단계: 탐욕적 단어 단위 줄바꿈 ──
    lines: list[str] = []
    cur = ''
    for word in words:
        candidate = (cur + ' ' + word).strip() if cur else word
        if _px(candidate) > max_px and cur:
            lines.append(cur)
            # 단어 하나가 max_px 초과하면 글자 단위 강제 분리
            if _px(word) > max_px:
                tmp = ''
                for ch in word:
                    test = tmp + ch
                    if _px(test) > max_px and tmp:
                        lines.append(tmp)
                        tmp = ch
                    else:
                        tmp = test
                cur = tmp
            else:
                cur = word
        else:
            cur = candidate
    if cur:
        lines.append(cur)

    # ── 2단계: widow 방지 — 마지막 줄이 단어 1개면 앞 줄에서 하나 당겨옴 ──
    if len(lines) >= 2:
        last_words = lines[-1].split(' ')
        prev_words = lines[-2].split(' ')
        if len(last_words) == 1 and len(prev_words) >= 2:
            moved = prev_words[-1]
            new_last = moved + ' ' + lines[-1]
            # 옮긴 줄이 max_px 안에 들어올 때만 적용
            if _px(new_last) <= max_px:
                lines[-2] = ' '.join(prev_words[:-1])
                lines[-1] = new_last

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

    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d  = ImageDraw.Draw(ov)

    br, bg_, bb = _hex_rgb(brand_color)

    # ── 상단 타이틀 배너 (0~22%) ────────────────────────────
    if overlay_title:
        top_h = int(H * 0.22)
        # 브랜드 컬러 tint 그라디언트
        for y in range(0, top_h):
            ratio = 1 - (y / top_h)
            r = int(br * 0.3 * ratio)
            g = int(bg_ * 0.3 * ratio)
            b_c = int(bb * 0.3 * ratio)
            a = int(210 * ratio)
            d.line([(0, y), (W, y)], fill=(r, g, b_c, a))

        tf = _font(bold=True, size=int(H * 0.080))
        lines = _wrap_text(overlay_title, tf, int(W * 0.88))[:2]
        ty = int(H * 0.028)
        for ln in lines:
            bb_box = tf.getbbox(ln)
            lw = bb_box[2] - bb_box[0]
            tx = (W - lw) // 2
            _draw_text_stroke(d, (tx, ty), ln, tf,
                              fill=(255, 230, 0, 255),
                              stroke_fill=(0, 0, 0, 255), stroke_w=8)
            ty += int((bb_box[3] - bb_box[1]) * 1.25)

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

        # 하단 자막 카드 배경
        card_top = int(H * 0.795)
        card_bot = H - 52
        d.rectangle([(0, card_top), (W, card_bot)], fill=(0, 0, 0, 160))

        # 브랜드 컬러 바
        d.rectangle([(0, H - 50), (W, H)], fill=(br, bg_, bb, 255))

        bf = _font(bold=True, size=int(H * 0.048))
        max_w = int(W * 0.88)
        lines = _wrap_text(overlay_body, bf, max_w)[:3]
        ty = card_top + int(H * 0.012)
        for ln in lines:
            bb_box = bf.getbbox(ln)
            lw = bb_box[2] - bb_box[0]
            tx = (W - lw) // 2
            _draw_text_stroke(d, (tx, ty), ln, bf,
                              fill=(255, 255, 255, 255),
                              stroke_fill=(0, 0, 0, 220), stroke_w=5)
            ty += int((bb_box[3] - bb_box[1]) * 1.40)

    combined = Image.alpha_composite(img, ov)
    result = _jpeg_b64(combined)
    # PIL 이미지 명시적 해제 (메모리 반환)
    img.close()
    ov.close()
    combined.close()
    return result


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

    br, bg_, bb = _hex_rgb(brand_color)

    # ── 상단 타이틀 배너 (0~18%) ────────────────────────
    if overlay_title:
        top_h = int(H * 0.18)
        for y in range(0, top_h):
            a = int(200 * (1 - y / top_h))
            d.line([(0, y), (W, y)], fill=(8, 8, 8, a))

        tf = _font(bold=True, size=int(H * 0.070))
        lines = _wrap_text(overlay_title, tf, int(W * 0.88))[:2]
        ty = int(H * 0.03)
        for ln in lines:
            bb_box = tf.getbbox(ln)
            lw = bb_box[2] - bb_box[0]
            tx = (W - lw) // 2
            _draw_text_stroke(d, (tx, ty), ln, tf,
                              fill=(255, 230, 0, 255),
                              stroke_fill=(0, 0, 0, 255), stroke_w=7)
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
    from services.instagram_service import _jpeg_b64
    result = _jpeg_b64(combined)
    bg_canvas.close()
    img.close()
    ov.close()
    combined.close()
    return result


# ════════════════════════════════════════════════════════
# 4. FFmpeg 조립
# ════════════════════════════════════════════════════════

def _ffmpeg(*args: str) -> subprocess.CompletedProcess:
    """FFmpeg 실행 — 좀비 프로세스 방지.

    - start_new_session=True: 워커와 별도 세션/프로세스 그룹 → 워커 OOM Kill 시
      자식 FFmpeg 도 OS가 함께 정리 (SIGHUP propagation).
    - timeout=300: 5분 초과 시 TimeoutExpired → 프로세스 강제 종료.
    """
    cmd = ['ffmpeg'] + list(args)
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # 좀비 방지 — 독립 프로세스 그룹
        )
        _register_proc(proc)  # 워커 종료 시 정리용 추적 등록
        stdout, stderr = proc.communicate(timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f'ffmpeg 오류:\n{stderr.decode(errors="replace")[-2000:]}')
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        if proc:
            try:
                import signal as _sig
                if hasattr(os, 'killpg'):  # POSIX(Linux)
                    os.killpg(os.getpgid(proc.pid), _sig.SIGKILL)
                else:
                    proc.kill()  # Windows fallback
            except Exception:
                proc.kill()
            proc.wait()
        raise RuntimeError('ffmpeg 타임아웃 (300초 초과) — 프로세스 강제 종료됨')
    finally:
        if proc:
            _unregister_proc(proc)  # 정상/비정상 종료 모두 추적 해제


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


def _get_video_duration(path: str) -> float:
    """ffprobe로 영상 길이(초) 반환."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', path],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        dur = data.get('format', {}).get('duration')
        if dur:
            return float(dur)
    except Exception:
        pass
    return 5.0


def _concat_with_crossfade(clip_paths: list, output_path: str, fade: float = 0.4) -> str:
    """여러 MP4 클립을 xfade 크로스페이드로 연결 → output_path.

    각 클립 duration을 ffprobe로 읽어 offset을 계산.
    """
    if len(clip_paths) == 1:
        import shutil as _sh
        _sh.copy2(clip_paths[0], output_path)
        return output_path

    durations = [_get_video_duration(p) for p in clip_paths]

    inputs = []
    for cp in clip_paths:
        inputs += ['-i', cp]

    n = len(clip_paths)
    fc_parts = []
    cumul = 0.0
    cur_v, cur_a = '[0:v]', '[0:a]'

    for k in range(n - 1):
        cumul += durations[k]
        offset = max(cumul - (k + 1) * fade, 0.1)
        is_last = (k == n - 2)
        out_v = '[vout]' if is_last else f'[v{k+1}]'
        out_a = '[aout]' if is_last else f'[a{k+1}]'
        fc_parts.append(
            f'{cur_v}[{k+1}:v]xfade=transition=fade:duration={fade}:offset={offset:.3f}{out_v}'
        )
        fc_parts.append(
            f'{cur_a}[{k+1}:a]acrossfade=d={fade}{out_a}'
        )
        cur_v, cur_a = out_v, out_a

    _ffmpeg(
        '-y',
        *inputs,
        '-filter_complex', ';'.join(fc_parts),
        '-map', '[vout]',
        '-map', '[aout]',
        '-c:v', 'libx264', '-preset', 'fast',
        '-c:a', 'aac', '-b:a', '128k',
        '-pix_fmt', 'yuv420p',
        output_path,
    )
    return output_path


def assemble_shorts_video(
    clip_data: list[dict],  # [{image_path, audio_path}, ...]
    output_path: str,
) -> str:
    """이미지+오디오 → Ken Burns 줌/패닝 + 크로스페이드 → MP4 (1080×1920).

    각 씬에 다른 방향의 Ken Burns 효과를 적용하고, 씬 간 0.4초 크로스페이드.
    """
    FPS  = 25
    FADE = 0.4   # 씬 간 크로스페이드 (초)

    # Ken Burns 패턴 — 씬마다 순환 적용 (d=총프레임수, fps=FPS)
    KB_PATTERNS = [
        # 0: 중앙 줌인 (1.0 → 1.15)
        "zoompan=z='1+on/d*0.15':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1080x1920:fps={fps}",
        # 1: 오른쪽 패닝 (1.1x)
        "zoompan=z='1.1':x='on/d*iw*0.06':y='ih/2-(ih/zoom/2)':d={d}:s=1080x1920:fps={fps}",
        # 2: 중앙 줌아웃 (1.15 → 1.0)
        "zoompan=z='1.15-on/d*0.15':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1080x1920:fps={fps}",
        # 3: 왼쪽 패닝 (1.1x)
        "zoompan=z='1.1':x='iw*0.06*(1-on/d)':y='ih/2-(ih/zoom/2)':d={d}:s=1080x1920:fps={fps}",
        # 4: 상단 줌인
        "zoompan=z='1+on/d*0.12':x='iw/2-(iw/zoom/2)':y='0':d={d}:s=1080x1920:fps={fps}",
    ]

    tmp_dir    = os.path.dirname(output_path)
    clip_paths = []

    # ── Step 1: 씬별 Ken Burns 클립 생성 ─────────────────────────
    for i, item in enumerate(clip_data):
        img_path   = item['image_path']
        audio_path = item['audio_path']
        clip_out   = os.path.join(tmp_dir, f'clip_{i:02d}.mp4')

        dur    = _get_audio_duration(audio_path)
        # 크로스페이드를 위해 마지막 씬 외에는 FADE만큼 더 생성
        total  = dur + FADE if i < len(clip_data) - 1 else dur
        frames = max(int(total * FPS) + 2, 2)

        pattern = KB_PATTERNS[i % len(KB_PATTERNS)]
        vf = (
            pattern.format(d=frames, fps=FPS) +
            ',scale=1080:1920:force_original_aspect_ratio=increase'
            ',crop=1080:1920,setsar=1'
        )

        _ffmpeg(
            '-y',
            '-loop', '1', '-i', img_path,
            '-i', audio_path,
            '-vf', vf,
            '-c:v', 'libx264', '-preset', 'fast',
            '-c:a', 'aac', '-b:a', '128k',
            '-pix_fmt', 'yuv420p',
            '-t', f'{total:.3f}',
            clip_out,
        )
        clip_paths.append(clip_out)

    # ── Step 2: xfade 크로스페이드로 연결 ────────────────────────
    return _concat_with_crossfade(clip_paths, output_path, fade=FADE)


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
    bgm_volume: float = 0.20,
    scene_images: list | None = None,
) -> None:
    """백그라운드 스레드에서 실행. Supabase creation 상태 업데이트.

    bgm_volume: 0.0 = BGM 없음, 0.01~1.0 = 볼륨 (기본 0.20)
    """
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
        import gc
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

            # 이미지 생성 — 사전 생성된 이미지가 있으면 재사용 (FLUX 생략)
            if scene_images and i < len(scene_images) and scene_images[i]:
                img_url = scene_images[i]
                logger.info('[shorts] 씬%d: 사전 생성 이미지 사용 (%s)', i, img_url[:60])
            else:
                style_mod  = SHORTS_STYLE_PRESETS.get(style, '')
                raw_prompt = scene.get('flux_prompt', '')
                from services.kling_service import ensure_english_prompt
                raw_prompt = ensure_english_prompt(raw_prompt)
                flux_p = raw_prompt + (f', {style_mod}' if style_mod else '') + _NO_CJK + _NO_ANATOMY
                img_url, _ = _generate_flux(flux_p, 'flux_standard', '1080x1920')

            # PIL 오버레이
            frame_b64 = composite_shorts_frame(
                img_url,
                scene.get('overlay_title', ''),
                scene.get('overlay_body', scene.get('narration', '')),
                brand_color,
                pil_size,
            )

            # 이미지 저장 후 즉시 base64 해제 (메모리 절약)
            img_path = os.path.join(tmp_dir, f'scene_{i:02d}.jpg')
            _, b64data = frame_b64.split(',', 1)
            with open(img_path, 'wb') as f:
                f.write(base64.b64decode(b64data))
            del frame_b64, b64data  # base64 문자열 즉시 해제

            # TTS
            narration = _normalize_tts_text(scene.get('narration', ''))
            mp3_bytes = tts_synthesize(narration, tts_api_key, voice_key, tts_speed)
            audio_path = os.path.join(tmp_dir, f'scene_{i:02d}.mp3')
            with open(audio_path, 'wb') as f:
                f.write(mp3_bytes)
            del mp3_bytes  # TTS bytes 즉시 해제

            clip_data.append({'image_path': img_path, 'audio_path': audio_path})
            gc.collect()  # 씬마다 GC — FLUX+PIL 잔여 객체 정리

        # FFmpeg 조립 (TTS만)
        _update('generating', {'progress': len(scenes), 'step': 'FFmpeg 영상 조립 중'})
        raw_mp4    = os.path.join(tmp_dir, 'shorts_raw.mp4')
        output_mp4 = os.path.join(tmp_dir, 'shorts.mp4')
        assemble_shorts_video(clip_data, raw_mp4)
        del clip_data  # clip_data 해제 (path 목록은 더 이상 불필요)
        gc.collect()

        # BGM 믹싱
        if bgm_volume > 0:
            bgm_path = pick_bgm(style)
            if bgm_path:
                _update('generating', {
                    'progress': len(scenes),
                    'step': f'BGM 믹싱 중 ({os.path.basename(bgm_path)})',
                })
                try:
                    mix_bgm_into_video(raw_mp4, bgm_path, output_mp4, bgm_volume)
                    logger.info('[shorts] BGM 믹싱 완료: %s', bgm_path)
                except Exception as bgm_err:
                    logger.warning('[shorts] BGM 믹싱 실패 (BGM 없이 진행): %s', bgm_err)
                    import shutil as _sh
                    _sh.copy2(raw_mp4, output_mp4)
            else:
                logger.info('[shorts] BGM 파일 없음 (static/sounds/bgm/ 폴더를 확인하세요)')
                import shutil as _sh
                _sh.copy2(raw_mp4, output_mp4)
        else:
            import shutil as _sh
            _sh.copy2(raw_mp4, output_mp4)

        # Supabase Storage 업로드 — 파일 스트리밍 (전체 bytes 메모리 로드 금지)
        _update('generating', {'progress': len(scenes) + 1, 'step': '업로드 중'})
        path = f'{user_id}/{uuid.uuid4().hex}_shorts.mp4'
        with open(output_mp4, 'rb') as f:
            supabase.storage.from_('creations').upload(
                path, f, {'content-type': 'video/mp4'}
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
        # 24시간 이상 된 고아 tmp 디렉토리 정리
        _cleanup_stale_tmp_dirs()
        # 최종 GC (워커 프로세스 메모리 반환)
        try:
            import gc as _gc
            _gc.collect()
        except Exception:
            pass


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
    bgm_volume: float = 0.20,
) -> None:
    def _run():
        if app:
            with app.app_context():
                run_shorts_pipeline(
                    creation_id=creation_id, user_id=user_id, scenes=scenes,
                    style=style, brand_color=brand_color, voice_key=voice_key,
                    tts_speed=tts_speed, supabase=supabase,
                    bgm_volume=bgm_volume,
                )
        else:
            run_shorts_pipeline(
                creation_id=creation_id, user_id=user_id, scenes=scenes,
                style=style, brand_color=brand_color, voice_key=voice_key,
                tts_speed=tts_speed, supabase=supabase,
                bgm_volume=bgm_volume,
            )

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ════════════════════════════════════════════════════════
# 6. Kling image2video 파이프라인
# ════════════════════════════════════════════════════════

def _make_text_overlay_png(
    overlay_title: str,
    overlay_body: str,
    brand_color: str,
    dest_path: str,
    pil_size: tuple = (1080, 1920),
) -> str:
    """투명 배경 위에 제목/자막 텍스트만 그린 PNG 저장 → dest_path 반환.

    Kling 영상 위에 FFmpeg overlay 필터로 합성.
    """
    from services.instagram_service import _hex_rgb
    W, H = pil_size
    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d  = ImageDraw.Draw(ov)
    br, bg_, bb = _hex_rgb(brand_color)

    # ── 상단 제목 그라디언트 배너 (0~22%) ──
    if overlay_title:
        top_h = int(H * 0.22)
        for y in range(top_h):
            ratio = 1 - (y / top_h)
            a = int(200 * ratio)
            d.line([(0, y), (W, y)], fill=(0, 0, 0, a))
        tf   = _font(bold=True, size=int(H * 0.075))
        lines = _wrap_text(overlay_title, tf, int(W * 0.88))[:2]
        ty   = int(H * 0.026)
        for ln in lines:
            bb_box = tf.getbbox(ln)
            lw = bb_box[2] - bb_box[0]
            tx = (W - lw) // 2
            _draw_text_stroke(d, (tx, ty), ln, tf,
                              fill=(255, 230, 0, 255),
                              stroke_fill=(0, 0, 0, 255), stroke_w=8)
            ty += int((bb_box[3] - bb_box[1]) * 1.25)

    # ── 하단 자막 배너 (78%~100%) ──
    if overlay_body:
        bot_start = int(H * 0.78)
        for y in range(bot_start, H):
            ratio = (y - bot_start) / (H - bot_start)
            a = int(220 * ratio)
            d.line([(0, y), (W, y)], fill=(0, 0, 0, a))
        # 브랜드 컬러 바 (맨 아래)
        d.rectangle([(0, H - 50), (W, H)], fill=(br, bg_, bb, 255))

        card_top = int(H * 0.795)
        d.rectangle([(0, card_top), (W, H - 52)], fill=(0, 0, 0, 160))
        bf   = _font(bold=True, size=int(H * 0.044))
        lines = _wrap_text(overlay_body, bf, int(W * 0.88))[:3]
        ty   = card_top + int(H * 0.012)
        for ln in lines:
            bb_box = bf.getbbox(ln)
            lw = bb_box[2] - bb_box[0]
            tx = (W - lw) // 2
            _draw_text_stroke(d, (tx, ty), ln, bf,
                              fill=(255, 255, 255, 255),
                              stroke_fill=(0, 0, 0, 220), stroke_w=5)
            ty += int((bb_box[3] - bb_box[1]) * 1.4)

    ov.save(dest_path, 'PNG')
    ov.close()
    return dest_path


def _overlay_text_on_video(
    video_path: str,
    text_png: str,
    audio_path: str,
    out_path: str,
) -> str:
    """Kling 영상 + 텍스트 오버레이 PNG + TTS 음성 → 최종 클립 MP4."""
    _ffmpeg(
        '-y',
        '-i', video_path,
        '-i', text_png,
        '-i', audio_path,
        '-filter_complex',
        '[0:v]scale=1080:1920:force_original_aspect_ratio=increase,'
        'crop=1080:1920,setsar=1[base];'
        '[base][1:v]overlay=0:0[vout]',
        '-map', '[vout]',
        '-map', '2:a',
        '-c:v', 'libx264', '-preset', 'ultrafast',
        '-c:a', 'aac', '-b:a', '128k',
        '-pix_fmt', 'yuv420p',
        '-shortest',
        out_path,
    )
    return out_path


def _extract_last_frame(video_path: str, output_png: str) -> str:
    """FFmpeg으로 영상의 마지막 프레임 추출 → PNG 저장 후 경로 반환.

    -sseof -0.5: 끝에서 0.5초 위치부터 탐색 → 마지막 프레임 캡처.
    """
    cmd = [
        'ffmpeg', '-y',
        '-sseof', '-0.5',
        '-i', video_path,
        '-vframes', '1',
        '-q:v', '2',
        output_png,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f'라스트프레임 추출 실패:\n{result.stderr[-500:]}')
    return output_png


def _upload_temp_frame(supabase, frame_path: str, creation_id: str, idx: int) -> str:
    """라스트프레임 PNG → Supabase Storage 임시 업로드 → 공개 URL 반환.

    경로: tmp/kling_frames/{creation_id}_{idx}.png
    Kling API가 URL만 받으므로 공개 URL 필요.
    최종 파이프라인 완료 후 자동 정리되지 않으므로 주기적 cleanup 필요.
    """
    storage_path = f'tmp/kling_frames/{creation_id}_{idx}.png'
    with open(frame_path, 'rb') as f:
        supabase.storage.from_('creations').upload(
            storage_path, f,
            {'content-type': 'image/png', 'x-upsert': 'true'},
        )
    return supabase.storage.from_('creations').get_public_url(storage_path)


def run_kling_shorts_pipeline(
    creation_id: str,
    user_id: str,
    scenes: list[dict],
    style: str,
    brand_color: str,
    voice_key: str,
    tts_speed: float,
    supabase,
    bgm_volume: float = 0.20,
    kling_model: str = 'kling-v1-6',
    product_image_url: str | None = None,
    ref_image_url: str | None = None,
    scene_images: list | None = None,
) -> None:
    """라스트프레임 체이닝 방식 Kling 쇼츠 파이프라인.

    흐름 (3씬 순차):
      FLUX 기준 이미지 1회 생성 (씬1·2용 공감 장면)
      → 씬1: 기준이미지 → Kling → 완료대기 → 라스트프레임 추출 → 임시업로드
      → 씬2: 라스트프레임1 → Kling → 완료대기 → 라스트프레임 추출 (→ 미사용)
      → 씬3: product_image_url(있으면) 또는 라스트프레임2 → Kling (제품 리빌)
      → 각 클립 TTS + 텍스트오버레이 → concat → BGM → Supabase 업로드

    product_image_url: 제품 실사 사진 URL — 마지막 씬의 입력 이미지로 사용.
                       없으면 FLUX 라스트프레임 체이닝 그대로 유지.
    """
    import gc
    from services.config_service import get_config
    from services.imagen_service import _generate_flux

    tmp_dir = os.path.join(tempfile.gettempdir(), f'maesil_kling_{creation_id}')
    os.makedirs(tmp_dir, exist_ok=True)

    def _update(status: str, extra: dict | None = None):
        row = {'status': status}
        if extra:
            row['output_data'] = extra
        try:
            supabase.table('creations').update(row).eq('id', creation_id).execute()
        except Exception as e:
            logger.error('[kling_chain] supabase update: %s', e)

    try:
        tts_api_key  = get_config('google_tts_api_key')
        kling_access = get_config('kling_access_key')
        kling_secret = get_config('kling_secret_key')
        kling_url    = get_config('kling_base_url') or 'https://api.klingai.com'

        if not tts_api_key:
            raise ValueError('google_tts_api_key 미설정')
        if not kling_access or not kling_secret:
            raise ValueError('kling_access_key / kling_secret_key 미설정')

        # Kling 모드: 최대 3씬 (순차처리 시간 제한)
        use_scenes = scenes[:3]
        n = len(use_scenes)
        pil_size   = (1080, 1920)
        style_mod  = SHORTS_STYLE_PRESETS.get(style, '')
        total_steps = n * 4 + 3  # 씬당 4단계(제출/대기/다운/조립) + 나머지 3단계

        from services.kling_service import (
            submit_image2video, wait_for_task, download_video, ensure_english_prompt,
        )

        # ── Step 1: 씬별 이미지 결정 (사전 생성 이미지 우선) ────────
        scene_img_urls: list[str] = []
        for i, scene in enumerate(use_scenes):
            # 마지막 씬 + 제품 이미지 있으면 제품 이미지 우선
            if i == n - 1 and product_image_url:
                scene_img_urls.append(product_image_url)
                logger.info('[kling_chain] 씬%d: 제품 이미지 사용 (리빌)', i + 1)
                continue
            # 사전 확인된 scene_images 있으면 재사용 (FLUX 생성 생략)
            if scene_images and i < len(scene_images) and scene_images[i]:
                scene_img_urls.append(scene_images[i])
                logger.info('[kling_chain] 씬%d: 사전 확인 이미지 사용', i + 1)
                continue
            # 씬별 FLUX 이미지 생성
            _update('generating', {
                'progress': i,
                'step': f'씬{i+1}/{n} 이미지 생성 중 (FLUX)',
            })
            raw_prompt = scene.get('flux_prompt', '')
            raw_prompt = ensure_english_prompt(raw_prompt)
            flux_p = raw_prompt + (f', {style_mod}' if style_mod else '') + _NO_CJK + _NO_ANATOMY
            img_url, _ = _generate_flux(flux_p, 'flux_standard', '1080x1920')
            scene_img_urls.append(img_url)
            logger.info('[kling_chain] 씬%d FLUX 이미지 생성: %s', i + 1, img_url[:60])
            gc.collect()

        # ── Steps 2~4: 씬별 Kling 순차 처리 ─────────────────────────
        kling_clips = []

        for i, scene in enumerate(use_scenes):
            role      = scene.get('role', 'hook')
            step_base = n + i * 3  # FLUX 생성 n단계 이후

            # ── Kling 제출 ──────────────────────────────────────
            _update('generating', {
                'progress': step_base,
                'step': f'씬{i+1}/{n} Kling 영상 생성 제출 중',
            })
            if i > 0:
                time.sleep(3)  # rate limit 회피

            task_id = submit_image2video(
                image_url=scene_img_urls[i],
                scene_role=role,
                access_key=kling_access,
                secret_key=kling_secret,
                model=kling_model,
                duration=5,
                base_url=kling_url,
            )
            logger.info('[kling_chain] 씬%d 제출: task_id=%s role=%s', i + 1, task_id, role)

            # ── 완료 대기 ───────────────────────────────────────
            _update('generating', {
                'progress': step_base + 1,
                'step': f'씬{i+1}/{n} Kling 처리 중 (약 3~5분)',
            })

            def _on_progress(elapsed: float, _i=i, _n=n, _sb=step_base):
                _update('generating', {
                    'progress': _sb + 1,
                    'step': f'씬{_i+1}/{_n} Kling 처리 중 ({int(elapsed)}초 경과)',
                })

            video_url = wait_for_task(
                task_id, kling_access, kling_secret,
                base_url=kling_url,
                timeout=600,
                poll_interval=12,
                on_progress=_on_progress,
            )
            logger.info('[kling_chain] 씬%d 완료: %s', i + 1, video_url)

            # ── 영상 다운로드 ────────────────────────────────────
            _update('generating', {
                'progress': step_base + 2,
                'step': f'씬{i+1}/{n} 다운로드 중',
            })
            kling_mp4 = os.path.join(tmp_dir, f'kling_{i:02d}.mp4')
            download_video(video_url, kling_mp4)
            kling_clips.append(kling_mp4)
            gc.collect()

        # ── Step 3: 씬별 TTS + 텍스트 오버레이 조립 ──────────────
        clip_data = []
        for i, (scene, kling_mp4) in enumerate(zip(use_scenes, kling_clips)):
            _update('generating', {
                'progress': n * 4 + i,
                'step': f'씬{i+1} 조립 중 (TTS + 텍스트)',
            })

            # 텍스트 오버레이 PNG (투명 배경)
            text_png = os.path.join(tmp_dir, f'text_{i:02d}.png')
            _make_text_overlay_png(
                scene.get('overlay_title', ''),
                scene.get('overlay_body', scene.get('narration', '')),
                brand_color,
                text_png,
                pil_size,
            )

            # TTS 음성
            narration  = _normalize_tts_text(scene.get('narration', ''))
            mp3_bytes  = tts_synthesize(narration, tts_api_key, voice_key, tts_speed)
            audio_path = os.path.join(tmp_dir, f'tts_{i:02d}.mp3')
            with open(audio_path, 'wb') as f:
                f.write(mp3_bytes)
            del mp3_bytes

            # FFmpeg: Kling 영상 + 텍스트 PNG + TTS → 최종 클립
            clip_out = os.path.join(tmp_dir, f'clip_{i:02d}.mp4')
            _overlay_text_on_video(kling_mp4, text_png, audio_path, clip_out)
            clip_data.append(clip_out)
            gc.collect()

        # ── Step 4: FFmpeg concat ──────────────────────────────────
        _update('generating', {'progress': total_steps - 2, 'step': '영상 합치는 중'})
        raw_mp4 = os.path.join(tmp_dir, 'kling_raw.mp4')
        _concat_with_crossfade(clip_data, raw_mp4, fade=0.4)

        # ── Step 5: BGM 믹싱 ───────────────────────────────────────
        output_mp4 = os.path.join(tmp_dir, 'kling_final.mp4')
        if bgm_volume > 0:
            bgm_path = pick_bgm(style)
            if bgm_path:
                _update('generating', {'progress': total_steps - 1, 'step': 'BGM 믹싱 중'})
                try:
                    mix_bgm_into_video(raw_mp4, bgm_path, output_mp4, bgm_volume)
                except Exception as bgm_e:
                    logger.warning('[kling_chain] BGM 실패 (무시): %s', bgm_e)
                    import shutil as _sh
                    _sh.copy2(raw_mp4, output_mp4)
            else:
                import shutil as _sh
                _sh.copy2(raw_mp4, output_mp4)
        else:
            import shutil as _sh
            _sh.copy2(raw_mp4, output_mp4)

        # ── Step 6: Supabase Storage 업로드 ───────────────────────
        _update('generating', {'progress': total_steps, 'step': '업로드 중'})
        path = f'{user_id}/{uuid.uuid4().hex}_kling_shorts.mp4'
        with open(output_mp4, 'rb') as f:
            supabase.storage.from_('creations').upload(
                path, f, {'content-type': 'video/mp4'}
            )
        final_url = supabase.storage.from_('creations').get_public_url(path)

        _update('done', {
            'video_url': final_url,
            'progress':  total_steps,
            'engine':    'kling',
            'scenes_used': n,
            'chaining':  True,
        })
        logger.info('[kling_chain] 완료: %s → %s (%d씬)', creation_id, final_url, n)

    except Exception as e:
        logger.error('[kling_chain] 오류 (%s): %s', creation_id, e)
        _update('failed', {'error': str(e)})
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _cleanup_stale_tmp_dirs()
        try:
            import gc as _gc
            _gc.collect()
        except Exception:
            pass
