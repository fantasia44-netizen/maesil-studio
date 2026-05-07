"""BGM 합성 스크립트 — ffmpeg sine/amix 필터로 분위기별 배경음 생성.

빌드 커맨드에서 호출:
    python scripts/generate_bgm.py

생성 파일:
    static/audio/bgm/bgm_lofi_chill.mp3
    static/audio/bgm/bgm_upbeat_pop.mp3
    static/audio/bgm/bgm_cinematic_calm.mp3
"""
import os
import subprocess
import sys

OUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'static', 'audio', 'bgm')


def _ffmpeg(*args):
    cmd = ['ffmpeg', '-y'] + list(args)
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(f'[BGM] ffmpeg 오류: {result.stderr.decode()[:300]}', file=sys.stderr)
        return False
    return True


def generate_lofi(out_path: str, duration: int = 120):
    """로파이 칠 — 느리고 따뜻한 코드 진행 (Am / F / C / G)."""
    # Am: 220, 261, 330  | F: 174, 220, 261  | C: 261, 330, 392  | G: 196, 247, 330
    # 4코드 × 4박 = 16박, BPM=70 → beat=60/70≈0.857s, 16박=13.7s 반복
    beat = 60 / 70  # 0.857s
    bar  = beat * 4  # 3.43s

    # 각 코드 wav 생성 후 concat
    chords = [
        # (root, third, fifth, 길이)
        (220.0, 261.6, 329.6, bar),  # Am
        (174.6, 220.0, 261.6, bar),  # F
        (261.6, 329.6, 392.0, bar),  # C
        (196.0, 246.9, 329.6, bar),  # G
    ]
    inputs, filter_parts = [], []
    for i, (r, t, f, dur) in enumerate(chords):
        vol = 0.08
        expr = (
            f'{vol}*sin(2*PI*{r}*t)'
            f'+{vol*0.6}*sin(2*PI*{r*2}*t)'           # 옥타브 위
            f'+{vol*0.5}*sin(2*PI*{t}*t)'
            f'+{vol*0.4}*sin(2*PI*{f}*t)'
            f'+{vol*0.2}*sin(2*PI*{r*0.5}*t)'          # 옥타브 아래 베이스
        )
        inputs += ['-f', 'lavfi', '-i', f'aevalsrc={expr}:s=44100:d={dur}']
        filter_parts.append(f'[{i}]')

    filt = ''.join(filter_parts) + f'concat=n={len(chords)}:v=0:a=1[out]'
    # 반복해서 duration 만큼
    loop_expr = (
        f'{0.08}*sin(2*PI*220*t)'
        f'+{0.05}*sin(2*PI*261.6*t)'
        f'+{0.04}*sin(2*PI*329.6*t)'
        f'+{0.03}*sin(2*PI*110*t)'
        f'+{0.015}*sin(2*PI*440*t)'
    )
    return _ffmpeg(
        '-f', 'lavfi', '-i',
        f'aevalsrc={loop_expr}:s=44100:d={duration}',
        '-af', 'afade=t=in:d=3,afade=t=out:st={dur2}:d=3,lowpass=f=3000,equalizer=f=200:width_type=o:width=2:g=4'.format(dur2=duration-3),
        '-c:a', 'libmp3lame', '-b:a', '128k', '-ar', '44100',
        out_path,
    )


def generate_upbeat(out_path: str, duration: int = 120):
    """업비트 팝 — 밝고 경쾌한 C장조 코드 (C / G / Am / F), BPM=120."""
    beat = 60 / 120  # 0.5s
    bar  = beat * 4  # 2.0s
    vol  = 0.07
    expr = (
        f'{vol}*sin(2*PI*261.6*t)'    # C4
        f'+{vol*0.8}*sin(2*PI*329.6*t)'  # E4
        f'+{vol*0.7}*sin(2*PI*392.0*t)'  # G4
        f'+{vol*0.4}*sin(2*PI*523.2*t)'  # C5 (옥타브)
        f'+{vol*0.3}*sin(2*PI*130.8*t)'  # C3 베이스
        f'+{vol*0.15}*sin(2*PI*784.0*t)' # G5 하이
    )
    return _ffmpeg(
        '-f', 'lavfi', '-i', f'aevalsrc={expr}:s=44100:d={duration}',
        '-af', f'afade=t=in:d=2,afade=t=out:st={duration-2}:d=2,'
               'highpass=f=80,equalizer=f=3000:width_type=o:width=2:g=2',
        '-c:a', 'libmp3lame', '-b:a', '128k', '-ar', '44100',
        out_path,
    )


def generate_cinematic(out_path: str, duration: int = 120):
    """시네마틱 칼름 — 깊고 웅장한 단음 드론 (Am, 느린 변화)."""
    vol = 0.06
    # Am 드론: A2(110) + E3(165) + A3(220) + 저음 보강
    expr = (
        f'{vol}*sin(2*PI*110*t)'
        f'+{vol*0.7}*sin(2*PI*165*t)'
        f'+{vol*0.5}*sin(2*PI*220*t)'
        f'+{vol*0.3}*sin(2*PI*55*t)'
        f'+{vol*0.15}*sin(2*PI*440*t)'
        # 느린 비브라토 (0.3Hz)
        f'+{vol*0.1}*sin(2*PI*(110+3*sin(2*PI*0.3*t))*t)'
    )
    return _ffmpeg(
        '-f', 'lavfi', '-i', f'aevalsrc={expr}:s=44100:d={duration}',
        '-af', f'afade=t=in:d=5,afade=t=out:st={duration-5}:d=5,'
               'lowpass=f=800,equalizer=f=100:width_type=o:width=2:g=6,'
               'aecho=0.8:0.9:500:0.3',
        '-c:a', 'libmp3lame', '-b:a', '128k', '-ar', '44100',
        out_path,
    )


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    tasks = [
        ('bgm_lofi_chill.mp3',    generate_lofi,      '로파이 칠'),
        ('bgm_upbeat_pop.mp3',    generate_upbeat,    '업비트 팝'),
        ('bgm_cinematic_calm.mp3', generate_cinematic, '시네마틱 칼름'),
    ]

    for fname, fn, label in tasks:
        out = os.path.join(OUT_DIR, fname)
        if os.path.exists(out):
            print(f'[BGM] {label} — 이미 있음, 스킵')
            continue
        print(f'[BGM] {label} 생성 중...')
        ok = fn(out)
        if ok:
            size = os.path.getsize(out) // 1024
            print(f'[BGM] {label} 완료 ({size}KB)')
        else:
            print(f'[BGM] {label} 생성 실패 (ffmpeg 없음 — 스킵)')


if __name__ == '__main__':
    main()
