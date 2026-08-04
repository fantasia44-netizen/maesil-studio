"""쿠파스 스튜디오 — 음성(TTS) + 타임라인 자막 합성 (B·C단계)

무음 영상 + 멘트 세그먼트 → 세그먼트별 Google TTS → 음성 길이로 타이밍 계산
→ ASS 자막을 영상에 번인 + 나레이션 오디오 합성 → 최종 MP4.

쇼츠 인프라(Google TTS·폰트·FFmpeg 헬퍼)를 재활용한다.
FFmpeg 는 Render 워커에 libass 포함(자막) + fonts-nanum(한글) 설치돼 있음.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import tempfile

import requests

from services.shorts_service import (
    _get_audio_duration, _ffmpeg, _ensure_font, _normalize_tts_text,
)

logger = logging.getLogger(__name__)

# ── 음성: Google Chirp3-HD(자연스러움↑) 우선, 실패 시 Neural2 폴백 ─────────
#    (locale, voiceName, is_chirp) — Chirp3-HD 는 pitch 파라미터 미지원
COUPAS_VOICES = {
    'ko_female_1': ('ko-KR', 'ko-KR-Chirp3-HD-Aoede', True),   # 여성·자연
    'ko_female_2': ('ko-KR', 'ko-KR-Chirp3-HD-Leda',  True),   # 여성·밝은
    'ko_male_1':   ('ko-KR', 'ko-KR-Chirp3-HD-Charon', True),  # 남성·차분
    'ko_male_2':   ('ko-KR', 'ko-KR-Chirp3-HD-Puck',   True),  # 남성·경쾌
    # Neural2 (폴백/호환)
    'female_natural': ('ko-KR', 'ko-KR-Neural2-A', False),
    'female_bright':  ('ko-KR', 'ko-KR-Neural2-B', False),
    'male_calm':      ('ko-KR', 'ko-KR-Neural2-C', False),
    'male_clear':     ('ko-KR', 'ko-KR-Wavenet-C', False),
}
_CHIRP_FALLBACK = {
    'ko_female_1': 'female_natural', 'ko_female_2': 'female_bright',
    'ko_male_1': 'male_calm', 'ko_male_2': 'male_clear',
}


def _synth(text: str, api_key: str, voice_key: str, speed: float) -> bytes:
    """Google TTS → MP3. Chirp3-HD/Neural2 모두 지원 (Chirp 는 pitch 생략)."""
    lang, name, is_chirp = COUPAS_VOICES.get(voice_key, COUPAS_VOICES['ko_female_1'])
    audio_cfg = {'audioEncoding': 'MP3', 'speakingRate': max(0.25, min(2.0, speed))}
    if not is_chirp:
        audio_cfg['pitch'] = 0.0
    resp = requests.post(
        f'https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}',
        json={'input': {'text': text},
              'voice': {'languageCode': lang, 'name': name},
              'audioConfig': audio_cfg},
        timeout=25)
    resp.raise_for_status()
    b64 = resp.json().get('audioContent', '')
    if not b64:
        raise ValueError('Google TTS 응답에 audioContent가 없습니다.')
    return base64.b64decode(b64)

MAX_SEGMENTS = 12


def _strip_emoji(text: str) -> str:
    """TTS 입력에서 이모지/픽토그램 제거 (자막에는 그대로 남김).

    유니코드 카테고리 So(기타 기호)·Sk(수식 기호) + 변형선택자/ZWJ 제거.
    한글·영문·일반 문장부호는 유지.
    """
    import unicodedata
    drop = {'️', '‍', '⃣'}
    out = ''.join(
        c for c in text
        if c not in drop and unicodedata.category(c) not in ('So', 'Sk'))
    return re.sub(r'\s{2,}', ' ', out).strip()


def download(url: str, dest: str, timeout: int = 60) -> str:
    """공개 URL(무음 영상)을 로컬로 스트리밍 다운로드."""
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if chunk:
                    f.write(chunk)
    if os.path.getsize(dest) < 1024:
        raise ValueError('영상 다운로드 실패(빈 파일).')
    return dest


def _probe_wh(path: str) -> tuple[int, int]:
    """ffprobe 로 영상 해상도(w,h) 반환. 실패 시 1080x1920 가정."""
    import json as _json
    import subprocess as _sp
    try:
        r = _sp.run(['ffprobe', '-v', 'quiet', '-print_format', 'json',
                     '-select_streams', 'v:0', '-show_streams', path],
                    capture_output=True, text=True, timeout=20)
        s = (_json.loads(r.stdout or '{}').get('streams') or [{}])[0]
        w, h = int(s.get('width') or 0), int(s.get('height') or 0)
        if w and h:
            return w, h
    except Exception:
        pass
    return 1080, 1920


def _ass_ts(sec: float) -> str:
    """초 → ASS 타임스탬프 H:MM:SS.cs"""
    cs = int(round(max(0.0, sec) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'


def _accent_numbers(text: str) -> str:
    """훅 문구의 숫자를 오렌지로 강조 (\\c 는 &HBBGGRR: 오렌지=&H00A5FF, 흰색=&H00FFFFFF)."""
    import re as _re
    def repl(m):
        return '{\\c&H00A5FF&}' + m.group(0) + '{\\c&H00FFFFFF&}'
    return _re.sub(r'\d[\d,]*', repl, text)


# 자막 스타일 프리셋
CAPTION_STYLES = {
    'outline': {'label': '검정 외곽선', 'border': 1, 'color': '&H00000000', 'thick': 7, 'shadow': 1},
    'cyan':    {'label': '파랑 외곽선', 'border': 1, 'color': '&H00FF901E', 'thick': 7, 'shadow': 1},
    'box':     {'label': '반투명 박스', 'border': 3, 'color': '&H80202020', 'thick': 5, 'shadow': 0},
}
DEFAULT_CAPTION_STYLE = 'outline'


def _build_ass(timed: list[dict], w: int, h: int, style: str = DEFAULT_CAPTION_STYLE,
               sub_pos: str = 'bottom') -> str:
    """세그먼트 타이밍 → ASS 자막.

    Sub = 흘러가는 자막(위치 선택), Hook = 첫 훅(상단 대형 + 숫자 강조).
    style: outline(검정 외곽선) | cyan(파랑 외곽선) | box(반투명 박스).
    sub_pos: bottom(하단) | center(중앙) | top(상단).
    """
    p = CAPTION_STYLES.get(style, CAPTION_STYLES[DEFAULT_CAPTION_STYLE])
    base = max(30, int(h / 16))
    hook_fs = int(base * 1.45)
    thick = max(2, base // p['thick'])
    hook_marginv = int(h * 0.12)
    # 자막 위치 → (Alignment, MarginV)
    _POS = {'bottom': (2, int(h * 0.22)), 'center': (5, 0), 'top': (8, int(h * 0.15))}
    sub_align, sub_marginv = _POS.get(sub_pos, _POS['bottom'])
    WHITE = '&H00FFFFFF'
    SHADOWCOL = '&H90000000'
    bs, col, sh = p['border'], p['color'], p['shadow']

    def _style(name, fs, align, mv):
        return (f'Style: {name},NanumGothic,{fs},{WHITE},{WHITE},{col},{SHADOWCOL},'
                f'-1,0,0,0,100,100,0,0,{bs},{thick},{sh},{align},60,60,{mv},1')

    header = (
        '[Script Info]\n'
        'ScriptType: v4.00+\n'
        f'PlayResX: {w}\nPlayResY: {h}\n'
        'WrapStyle: 0\nScaledBorderAndShadow: yes\n\n'
        '[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
        'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, '
        'BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
        + _style('Sub', base, sub_align, sub_marginv) + '\n'
        + _style('Hook', hook_fs, 8, hook_marginv) + '\n\n'
        '[Events]\n'
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    )
    lines = []
    for seg in timed:
        txt = (seg.get('text') or '').replace('\n', ' ').replace('{', '(').replace('}', ')')
        is_hook = seg.get('role') == 'hook'
        if is_hook:
            txt = _accent_numbers(txt)
        lines.append(
            f"Dialogue: 0,{_ass_ts(seg['start'])},{_ass_ts(seg['end'])},"
            f"{'Hook' if is_hook else 'Sub'},,0,0,0,,{txt}")
    return header + '\n'.join(lines) + '\n'


def render_narration(muted_local: str, segments: list, voice_key: str,
                     tts_speed: float, tts_api_key: str,
                     out_path: str, work_dir: str,
                     caption_style: str = DEFAULT_CAPTION_STYLE,
                     bgm_path: str | None = None, bgm_volume: float = 0.18,
                     sub_pos: str = 'bottom', bgm_start: float = 0.0) -> dict:
    """무음 영상에 TTS 나레이션 + 타임라인 자막(+선택 BGM)을 합성해 out_path 로 저장.

    반환: {duration, seg_count, width, height, bgm}
    """
    # 한글 폰트 확보 + libass 가 참조할 폰트 디렉토리 확정
    _ensure_font('NanumGothicBold.ttf')
    font_path = _ensure_font('NanumGothic.ttf')
    fonts_dir = os.path.dirname(font_path) if font_path else None
    w, h = _probe_wh(muted_local)

    # 1) 세그먼트별 TTS → mp3 + 길이 측정 → 타이밍 누적
    seg_files: list[str] = []
    timed: list[dict] = []
    t = 0.0
    used = 0
    for i, seg in enumerate(segments[:MAX_SEGMENTS]):
        raw = (seg.get('text') or '').strip() if isinstance(seg, dict) else str(seg).strip()
        if not raw:
            continue
        spoken = _strip_emoji(_normalize_tts_text(raw))   # 이모지는 음성으로 안 읽음
        if not spoken:
            continue
        try:
            mp3 = _synth(spoken, tts_api_key, voice_key, tts_speed)
        except requests.HTTPError as he:
            code = getattr(he.response, 'status_code', '?')
            fb = _CHIRP_FALLBACK.get(voice_key)
            if fb and code in (400, 404):
                # Chirp3-HD 미지원/오류 → Neural2 로 자동 전환(이후 세그먼트도 유지)
                logger.warning('[coupas_render] Chirp3-HD(%s) 오류 %s → Neural2 폴백', voice_key, code)
                voice_key = fb
                mp3 = _synth(spoken, tts_api_key, voice_key, tts_speed)
            else:
                body = ''
                try:
                    body = (he.response.text or '')[:400]
                except Exception:
                    pass
                if code == 403:
                    hint = ('Google TTS 403(권한 거부). 확인: ①Cloud Text-to-Speech API 사용설정(Enable) '
                            '②API 키의 애플리케이션 제한(HTTP리퍼러/IP) 해제 또는 서버 허용 ③결제(billing) 연결. ')
                else:
                    hint = f'Google TTS HTTP {code}. '
                raise RuntimeError(f'{hint}상세: {body or he}')
        p = os.path.join(work_dir, f'seg{used}.mp3')
        with open(p, 'wb') as f:
            f.write(mp3)
        dur = _get_audio_duration(p)
        timed.append({'text': raw, 'start': t, 'end': t + dur})
        seg_files.append(p)
        t += dur
        used += 1

    if not seg_files:
        raise ValueError('음성으로 만들 멘트가 없습니다.')
    total = max(t, 1.0)

    # 2) 나레이션 오디오 합성 (concat demuxer — 동일 인코딩 mp3 이어붙이기)
    list_path = os.path.join(work_dir, 'concat.txt')
    with open(list_path, 'w', encoding='utf-8') as f:
        for p in seg_files:
            f.write(f"file '{p}'\n")
    narration = os.path.join(work_dir, 'narration.mp3')
    _ffmpeg('-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', narration)

    # 3) ASS 자막 파일
    ass_path = os.path.join(work_dir, 'subs.ass')
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(_build_ass(timed, w, h, caption_style, sub_pos))

    # 4) 최종 합성 — 무음영상(길이 부족시 루프) + 자막 번인 + 나레이션(+BGM), 나레이션 길이로 컷
    #    fontsdir 로 나눔폰트 위치를 libass 에 명시 (fontconfig 미스로 한글 □ 깨짐 방지)
    ass_f = f'ass={ass_path}'
    if fonts_dir:
        ass_f = f'ass={ass_path}:fontsdir={fonts_dir}'

    common_tail = [
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', out_path,
    ]
    if bgm_path and os.path.exists(bgm_path):
        # 나레이션(원음) + BGM(볼륨↓, 시작지점 skip, 0.4s 페이드인) amix.
        vol = max(0.0, min(1.0, bgm_volume))
        fc = (f'[0:v]{ass_f}[v];'
              f'[2:a]volume={vol:.3f},afade=t=in:st=0:d=0.4[bg];'
              f'[1:a][bg]amix=inputs=2:duration=first:normalize=0[a]')
        _ffmpeg('-y',
                '-stream_loop', '-1', '-i', muted_local,
                '-i', narration,
                '-ss', f'{max(0.0, bgm_start):.2f}', '-stream_loop', '-1', '-i', bgm_path,
                '-t', f'{total:.3f}',
                '-filter_complex', fc,
                '-map', '[v]', '-map', '[a]',
                *common_tail)
    else:
        _ffmpeg('-y',
                '-stream_loop', '-1', '-i', muted_local,
                '-i', narration,
                '-t', f'{total:.3f}',
                '-vf', ass_f,
                '-map', '0:v:0', '-map', '1:a:0',
                *common_tail)

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError('최종 영상 생성 실패(빈 파일).')

    return {'duration': round(total, 2), 'seg_count': used, 'width': w, 'height': h,
            'bgm': bool(bgm_path)}
