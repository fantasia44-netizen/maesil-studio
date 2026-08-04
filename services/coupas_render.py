"""쿠파스 스튜디오 — 음성(TTS) + 타임라인 자막 합성 (B·C단계)

무음 영상 + 멘트 세그먼트 → 세그먼트별 Google TTS → 음성 길이로 타이밍 계산
→ ASS 자막을 영상에 번인 + 나레이션 오디오 합성 → 최종 MP4.

쇼츠 인프라(Google TTS·폰트·FFmpeg 헬퍼)를 재활용한다.
FFmpeg 는 Render 워커에 libass 포함(자막) + fonts-nanum(한글) 설치돼 있음.
"""
from __future__ import annotations

import logging
import os
import tempfile

import requests

from services.shorts_service import (
    tts_synthesize, _get_audio_duration, _ffmpeg, _ensure_font, _normalize_tts_text,
)

logger = logging.getLogger(__name__)

MAX_SEGMENTS = 12


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


def _build_ass(timed: list[dict], w: int, h: int) -> str:
    """세그먼트 타이밍 → ASS 자막 문자열 (하단 중앙, 흰 글자 + 검은 외곽선)."""
    fontsize = max(28, int(h / 16))
    outline = max(2, fontsize // 14)
    marginv = int(h * 0.18)   # 하단에서 18% 위 — 릴스/쇼츠 하단 UI 회피
    header = (
        '[Script Info]\n'
        'ScriptType: v4.00+\n'
        f'PlayResX: {w}\nPlayResY: {h}\n'
        'WrapStyle: 2\nScaledBorderAndShadow: yes\n\n'
        '[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, '
        'BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
        f'Style: Default,NanumGothic,{fontsize},&H00FFFFFF,&H00202020,&H90000000,'
        f'-1,0,0,0,100,100,0,0,1,{outline},1,2,60,60,{marginv},1\n\n'
        '[Events]\n'
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    )
    lines = []
    for seg in timed:
        txt = (seg['text'] or '').replace('\n', ' ').replace('{', '(').replace('}', ')')
        lines.append(
            f"Dialogue: 0,{_ass_ts(seg['start'])},{_ass_ts(seg['end'])},Default,,0,0,0,,{txt}")
    return header + '\n'.join(lines) + '\n'


def render_narration(muted_local: str, segments: list, voice_key: str,
                     tts_speed: float, tts_api_key: str,
                     out_path: str, work_dir: str) -> dict:
    """무음 영상에 TTS 나레이션 + 타임라인 자막을 합성해 out_path 로 저장.

    반환: {duration, seg_count, width, height}
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
        spoken = _normalize_tts_text(raw)
        try:
            mp3 = tts_synthesize(spoken, tts_api_key, voice_key, tts_speed)
        except requests.HTTPError as he:
            code = getattr(he.response, 'status_code', '?')
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
        f.write(_build_ass(timed, w, h))

    # 4) 최종 합성 — 무음영상(길이 부족시 루프) + 자막 번인 + 나레이션, 나레이션 길이에 맞춰 컷
    #    fontsdir 로 나눔폰트 위치를 libass 에 명시 (fontconfig 미스로 한글이 □ 로 깨지는 것 방지)
    ass_vf = f'ass={ass_path}'
    if fonts_dir:
        ass_vf = f'ass={ass_path}:fontsdir={fonts_dir}'
    _ffmpeg(
        '-y',
        '-stream_loop', '-1', '-i', muted_local,   # 영상 (필요시 반복)
        '-i', narration,                            # 나레이션 오디오
        '-t', f'{total:.3f}',
        '-vf', ass_vf,
        '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        out_path,
    )
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError('최종 영상 생성 실패(빈 파일).')

    return {'duration': round(total, 2), 'seg_count': used, 'width': w, 'height': h}
