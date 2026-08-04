"""소스 영상 가져오기 — 쿠파스 스튜디오 1단계

원본(중국 상품) 영상을 스튜디오로 들여오는 모듈.

지원 입력:
  1. 파일 업로드 (항상 동작 — 가장 안정적인 폴백)
  2. 1688 / 타오바오 / 티몰 상품페이지 URL
     → 페이지 HTML에서 알리바바 CDN(cloud.video.taobao.com 등) MP4 URL을 추출해 다운로드

처리:
  - 다운로드 (용량 캡, 스트리밍)
  - ffprobe 로 메타데이터(길이/해상도/오디오 유무) 추출
  - 오디오 제거 (ffmpeg -an) — 이후 단계에서 TTS/BGM 을 새로 입힘

주의:
  - 더우인(抖音)은 yt-dlp 가 필요하며 안티봇으로 자주 깨짐 → Phase 2 에서 별도 처리.
  - 타오바오/티몰은 로그인/슬라이더 캡차로 HTML 추출이 막힐 수 있음 → 실패 시 업로드 폴백.
  - ffmpeg/ffprobe 는 워커 환경(Render)에 설치돼 있음. 로컬(Windows)에 없으면
    probe_video/strip_audio 는 graceful 하게 실패하고 URL 추출·다운로드만 동작.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────────────
MAX_VIDEO_BYTES = 200 * 1024 * 1024   # 200MB 다운로드 상한
DOWNLOAD_TIMEOUT = 60                  # 초
PAGE_TIMEOUT = 15                      # 초

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'zh-CN,zh;q=0.9,ko;q=0.8,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Cache-Control': 'no-cache',
    'Upgrade-Insecure-Requests': '1',
}

# 모바일 UA — 1688 데스크톱(detail.1688.com)은 안티봇으로 막히지만
# 모바일(m.1688.com)+모바일UA 는 상품 JSON(videoUrl 포함)을 내려준다.
_MOBILE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1'
)
# 페이지가 요청마다 들쭉날쭉(정상 ↔ 2KB 차단페이지) 하므로 재시도 필요
PAGE_FETCH_ATTEMPTS = 6

_RE_1688_OFFER = re.compile(r'/offer/(\d+)\.html', re.I)
_RE_VIDEO_ID = re.compile(r'"videoId"\s*:\s*"?(\d{6,})"?')
_RE_SELLER_ID = re.compile(r'(?:"sellerUserId"\s*:\s*"?|sellerUserId@)(\d{4,})')

# 알리바바 계열 영상 CDN 호스트 화이트리스트 (오탐 방지)
_ALI_VIDEO_HOSTS = (
    'cloud.video.taobao.com',
    'video.taobaocdn.com',
    'gw.alicdn.com',
    'video.1688.com',
    'cloud.video.alibaba.com',
    'valuevideo.alicdn.com',
)


# ── 플랫폼 감지 ────────────────────────────────────────────────
def is_direct_video_url(url: str) -> bool:
    """직접 영상 파일 링크인지 판별.

    사용자가 상품페이지에서 '영상 우클릭 → 동영상 주소 복사'로 얻는 CDN 링크
    (cloud.video.taobao.com/....mp4)가 여기 해당 — 안티봇/로그인 우회하는 가장 안정적 경로.
    """
    u = url.lower().split('?')[0]
    if u.endswith(('.mp4', '.mov', '.m4v', '.webm')):
        return True
    host = urlparse(url).netloc.lower()
    return any(h in host for h in _ALI_VIDEO_HOSTS) or host.endswith('alicdn.com')


def detect_source_platform(url: str) -> str:
    """상품 URL 의 플랫폼 감지."""
    host = urlparse(url).netloc.lower()
    if 'video.taobao.com' in host or host.endswith('alicdn.com') or 'video.1688.com' in host:
        return 'direct'
    if '1688.com' in host:
        return '1688'
    if 'tmall.com' in host:
        return 'tmall'
    if 'taobao.com' in host:
        return 'taobao'
    if 'douyin.com' in host or 'iesdouyin.com' in host:
        return 'douyin'
    if 'kuaishou.com' in host:
        return 'kuaishou'
    return 'unknown'


# ── HTML → 영상 URL 추출 ───────────────────────────────────────
def _normalize_escapes(text: str) -> str:
    """JSON 안에 이스케이프된 URL 복원: `\\/` → `/`, `\\u002F` → `/`."""
    return (text.replace('\\/', '/')
                .replace('\\u002F', '/')
                .replace('\\u002f', '/'))


# 우선순위: 명시적 videoUrl 필드 → CDN mp4 직접 매칭
_RE_VIDEO_FIELD = re.compile(
    r'"video(?:Url|_url|Path)"\s*:\s*"([^"]+?\.mp4[^"]*)"', re.I)
_RE_CDN_MP4 = re.compile(
    r'https?://[a-z0-9.\-]*\.(?:taobao|taobaocdn|alicdn|1688|alibaba)\.com/[^\s"\'<>\\)]+?\.mp4[^\s"\'<>\\)]*',
    re.I)
_RE_PROTO_REL_MP4 = re.compile(
    r'//[a-z0-9.\-]*\.(?:taobao|taobaocdn|alicdn|1688|alibaba)\.com/[^\s"\'<>\\)]+?\.mp4[^\s"\'<>\\)]*',
    re.I)


def extract_video_urls(page_html: str) -> list[str]:
    """상품페이지 HTML 에서 후보 영상 MP4 URL 목록 추출 (중복 제거, 우선순위 순)."""
    text = _normalize_escapes(page_html)
    found: list[str] = []

    def _add(u: str):
        u = u.strip()
        if u.startswith('//'):
            u = 'https:' + u
        # 호스트 화이트리스트 재검증
        host = urlparse(u).netloc.lower()
        if not any(h in host for h in _ALI_VIDEO_HOSTS) and not host.endswith('alicdn.com'):
            return
        if u not in found:
            found.append(u)

    for m in _RE_VIDEO_FIELD.finditer(text):
        _add(m.group(1))
    for m in _RE_CDN_MP4.finditer(text):
        _add(m.group(0))
    for m in _RE_PROTO_REL_MP4.finditer(text):
        _add(m.group(0))

    return found


def _mobile_1688_url(url: str) -> str:
    """detail.1688.com/offer/{id}.html → m.1688.com/offer/{id}.html (안티봇 우회)."""
    m = _RE_1688_OFFER.search(url)
    if m:
        return f'https://m.1688.com/offer/{m.group(1)}.html'
    return url


def _construct_ali_video_url(html: str) -> str | None:
    """videoUrl 직접 매칭 실패 시 videoId+sellerUserId 로 CDN URL 조합 (폴백)."""
    vid = _RE_VIDEO_ID.search(html)
    sel = _RE_SELLER_ID.search(html)
    if vid and sel:
        return (f'https://cloud.video.taobao.com/play/u/{sel.group(1)}'
                f'/p/2/e/6/t/1/{vid.group(1)}.mp4')
    return None


def fetch_page_video_urls(url: str, max_attempts: int = PAGE_FETCH_ATTEMPTS) -> list[str]:
    """상품 URL 을 열어 HTML 에서 영상 URL 후보를 추출.

    1688: 모바일 페이지(m.1688.com)+모바일UA 로 요청. 페이지가 요청마다
    정상/차단(2KB)으로 갈리므로 후보를 얻을 때까지 재시도한다.
    타오바오/티몰: 로그인 벽으로 서버 추출이 사실상 불가 — 시도는 하되 실패 시 상위에서 업로드 안내.
    """
    platform = detect_source_platform(url)

    fetch_url = url
    headers = dict(_HEADERS)
    if platform == '1688':
        fetch_url = _mobile_1688_url(url)
        headers['User-Agent'] = _MOBILE_UA
        headers['Referer'] = 'https://m.1688.com/'
    elif platform in ('taobao', 'tmall'):
        headers['User-Agent'] = _MOBILE_UA
        headers['Referer'] = 'https://main.m.taobao.com/'

    last_len = 0
    for attempt in range(max_attempts):
        try:
            resp = requests.get(fetch_url, headers=headers,
                                timeout=PAGE_TIMEOUT, allow_redirects=True)
            html = resp.text or ''
        except Exception as e:
            logger.warning('[video_import] 페이지 요청 실패(%d/%d): %s',
                           attempt + 1, max_attempts, e)
            time.sleep(0.8)
            continue

        last_len = len(html)
        urls = extract_video_urls(html)
        if not urls:
            c = _construct_ali_video_url(html)
            if c:
                urls = [c]
        if urls:
            logger.info('[video_import] %s → 영상 후보 %d개 (시도 %d회, len=%d)',
                        platform, len(urls), attempt + 1, last_len)
            return urls
        time.sleep(0.8)

    logger.info('[video_import] %s → 추출 실패 (시도 %d회, 마지막 len=%d)',
                platform, max_attempts, last_len)
    return []


# ── 다운로드 ──────────────────────────────────────────────────
def download_video(url: str, referer: str | None = None) -> str:
    """영상 URL 을 임시 파일로 스트리밍 다운로드. 반환: 로컬 임시 경로.

    - 용량 상한(MAX_VIDEO_BYTES) 초과 시 중단.
    - Content-Type 이 video/* 가 아니어도 확장자가 .mp4 면 허용.
    """
    headers = dict(_HEADERS)
    if referer:
        headers['Referer'] = referer

    tmp_dir = tempfile.mkdtemp(prefix='vimport_')
    out_path = os.path.join(tmp_dir, f'{uuid.uuid4().hex[:12]}.mp4')

    try:
        with requests.get(url, headers=headers, stream=True,
                          timeout=DOWNLOAD_TIMEOUT, allow_redirects=True) as r:
            r.raise_for_status()
            ctype = (r.headers.get('Content-Type') or '').lower()
            if 'video' not in ctype and '.mp4' not in url.lower() and 'octet-stream' not in ctype:
                raise ValueError(f'영상이 아닌 응답입니다 (Content-Type: {ctype or "unknown"})')

            total = 0
            with open(out_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise ValueError(
                            f'영상 용량이 상한({MAX_VIDEO_BYTES // (1024*1024)}MB)을 초과합니다.')
                    f.write(chunk)
    except Exception:
        _safe_rmtree(tmp_dir)
        raise

    if os.path.getsize(out_path) < 1024:
        _safe_rmtree(tmp_dir)
        raise ValueError('다운로드된 영상이 비어있습니다.')

    return out_path


def save_upload(file_storage) -> str:
    """업로드된 파일(werkzeug FileStorage)을 임시 파일로 저장. 반환: 로컬 임시 경로."""
    tmp_dir = tempfile.mkdtemp(prefix='vimport_')
    ext = 'mp4'
    if file_storage.filename and '.' in file_storage.filename:
        cand = file_storage.filename.rsplit('.', 1)[-1].lower()
        if cand in ('mp4', 'mov', 'webm', 'mkv', 'avi', 'm4v'):
            ext = cand
    out_path = os.path.join(tmp_dir, f'{uuid.uuid4().hex[:12]}.{ext}')
    file_storage.save(out_path)

    size = os.path.getsize(out_path)
    if size < 1024:
        _safe_rmtree(tmp_dir)
        raise ValueError('업로드된 영상이 비어있습니다.')
    if size > MAX_VIDEO_BYTES:
        _safe_rmtree(tmp_dir)
        raise ValueError(f'영상 용량이 상한({MAX_VIDEO_BYTES // (1024*1024)}MB)을 초과합니다.')
    return out_path


# ── ffprobe / ffmpeg ──────────────────────────────────────────
def probe_video(path: str) -> dict:
    """ffprobe 로 영상 메타데이터 추출.

    반환: {duration, width, height, has_audio, size_bytes, codec, ok, error?}
    ffprobe 가 없으면 ok=False + error 메시지 (다운로드 자체는 성공).
    """
    meta = {
        'duration': None, 'width': None, 'height': None,
        'has_audio': None, 'size_bytes': None, 'codec': None, 'ok': False,
    }
    try:
        meta['size_bytes'] = os.path.getsize(path)
    except OSError:
        pass

    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', '-show_streams', path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout or '{}')
    except FileNotFoundError:
        meta['error'] = 'ffprobe 미설치 (로컬 환경) — 워커에서는 정상 동작'
        return meta
    except Exception as e:
        meta['error'] = f'ffprobe 실패: {e}'
        return meta

    fmt = data.get('format', {})
    if fmt.get('duration'):
        try:
            meta['duration'] = round(float(fmt['duration']), 2)
        except (TypeError, ValueError):
            pass

    has_audio = False
    for s in data.get('streams', []):
        if s.get('codec_type') == 'video' and meta['width'] is None:
            meta['width'] = s.get('width')
            meta['height'] = s.get('height')
            meta['codec'] = s.get('codec_name')
        if s.get('codec_type') == 'audio':
            has_audio = True
    meta['has_audio'] = has_audio
    meta['ok'] = True
    return meta


def strip_audio(in_path: str, out_path: str | None = None) -> str:
    """원본 영상에서 오디오 트랙 제거 (재인코딩 없이 -c:v copy). 반환: 출력 경로.

    이후 단계에서 TTS 나레이션 + BGM + 효과음을 새로 입힘.
    """
    if out_path is None:
        base, _ = os.path.splitext(in_path)
        out_path = f'{base}_mute.mp4'

    try:
        proc = subprocess.run(
            ['ffmpeg', '-y', '-i', in_path, '-an', '-c:v', 'copy',
             '-movflags', '+faststart', out_path],
            capture_output=True, timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError('ffmpeg 미설치 — 오디오 제거는 워커 환경에서만 가능합니다.')
    if proc.returncode != 0:
        err = proc.stderr.decode(errors='replace')[-1500:] if proc.stderr else ''
        # copy 실패(컨테이너 호환 문제) 시 재인코딩 폴백
        proc2 = subprocess.run(
            ['ffmpeg', '-y', '-i', in_path, '-an', '-c:v', 'libx264',
             '-preset', 'veryfast', '-crf', '23',
             '-movflags', '+faststart', out_path],
            capture_output=True, timeout=300,
        )
        if proc2.returncode != 0:
            err2 = proc2.stderr.decode(errors='replace')[-1500:] if proc2.stderr else ''
            raise RuntimeError(f'오디오 제거 실패:\n{err}\n---\n{err2}')
    return out_path


# ── 오케스트레이션 ────────────────────────────────────────────
def import_from_url(page_url: str) -> dict:
    """상품 URL → 영상 다운로드 + 메타. 반환:
       {ok, local_path, source_url, candidates, meta, platform}  또는  {ok:False, error}
    """
    platform = detect_source_platform(page_url)
    if platform in ('douyin', 'kuaishou'):
        return {'ok': False, 'platform': platform,
                'error': '더우인/콰이쇼우는 아직 지원하지 않습니다(Phase 2). 파일 업로드를 이용하세요.'}

    # ① 직접 영상 링크(우클릭→동영상 주소 복사)면 스크래핑 없이 바로 다운로드 — 가장 안정적
    if is_direct_video_url(page_url):
        candidates = [page_url]
    # ② 상품페이지 URL이면 HTML에서 추출 시도 (안티봇으로 실패 가능 → 상위에서 안내)
    else:
        candidates = fetch_page_video_urls(page_url)

    if not candidates:
        hint = ('상품 URL 자동추출이 1688 차단에 막혔습니다. '
                '👉 브라우저에서 상품 영상 위 우클릭 → "동영상 주소 복사" 후 그 링크를 붙여넣으세요. '
                '(그래도 안 되면 파일 업로드)')
        return {'ok': False, 'platform': platform, 'candidates': [], 'error': hint}

    referer = page_url
    last_err = None
    for cand in candidates:
        try:
            local_path = download_video(cand, referer=referer)
            meta = probe_video(local_path)
            return {'ok': True, 'platform': platform, 'source_url': cand,
                    'candidates': candidates, 'local_path': local_path, 'meta': meta}
        except Exception as e:
            last_err = str(e)
            logger.warning('[video_import] 후보 다운로드 실패 %s: %s', cand[:80], e)
            continue

    return {'ok': False, 'platform': platform, 'candidates': candidates,
            'error': f'영상 다운로드에 실패했습니다: {last_err}'}


# ── Supabase Storage ──────────────────────────────────────────
# 'creations' = 프로젝트 전 기능(쇼츠 영상·배너·상품 등)이 공용으로 쓰는 버킷.
STORAGE_BUCKET = 'creations'
SOURCE_PREFIX = 'coupas/source'          # 원본(무음화) 영상 보관 경로 prefix
SOURCE_RETENTION_DAYS = 7                 # 원본 자동 삭제 기한


def source_storage_path(user_id: str, creation_id: str) -> str:
    """원본 영상 Storage 경로. 정리 잡이 prefix 로 스캔하므로 규칙을 지킬 것."""
    return f'{SOURCE_PREFIX}/{user_id}/{creation_id}.mp4'


def upload_file(supabase, local_path: str, dest_path: str,
                content_type: str = 'video/mp4') -> str:
    """로컬 파일을 Supabase Storage 에 업로드하고 공개 URL 반환."""
    with open(local_path, 'rb') as f:
        data = f.read()
    supabase.storage.from_(STORAGE_BUCKET).upload(
        dest_path, data,
        file_options={'content-type': content_type, 'upsert': 'true'},
    )
    return supabase.storage.from_(STORAGE_BUCKET).get_public_url(dest_path)


def delete_storage_paths(supabase, paths: list[str]) -> int:
    """Storage 오브젝트 삭제. 반환: 삭제 시도한 개수(실패 무시)."""
    paths = [p for p in paths if p]
    if not paths:
        return 0
    try:
        supabase.storage.from_(STORAGE_BUCKET).remove(paths)
    except Exception as e:
        logger.warning('[video_import] Storage 삭제 실패 %s: %s', paths, e)
    return len(paths)


def cleanup_expired_sources(supabase, days: int = SOURCE_RETENTION_DAYS) -> dict:
    """7일 경과한 원본(coupas_import) 영상을 Storage 에서 삭제.

    APScheduler 일일 잡에서 호출. creations 행은 이력 유지를 위해 남기되,
    output_data.source_deleted=True 로 마킹하고 storage_path 를 제거한다.
    반환: {scanned, deleted}
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        rows = (supabase.table('creations')
                .select('id, output_data')
                .eq('creation_type', 'coupas_import')
                .lt('created_at', cutoff)
                .limit(500)
                .execute()).data or []
    except Exception as e:
        logger.error('[video_import] 정리 대상 조회 실패: %s', e)
        return {'scanned': 0, 'deleted': 0, 'error': str(e)}

    deleted = 0
    for r in rows:
        od = r.get('output_data') or {}
        if od.get('source_deleted'):
            continue
        sp = od.get('storage_path')
        if not sp:
            continue
        delete_storage_paths(supabase, [sp])
        try:
            new_od = dict(od)
            new_od['source_deleted'] = True
            new_od['storage_path'] = None
            supabase.table('creations').update(
                {'output_data': new_od}).eq('id', r['id']).execute()
        except Exception as e:
            logger.warning('[video_import] 정리 마킹 실패 cid=%s: %s', r['id'], e)
        deleted += 1

    logger.info('[video_import] 원본 정리: 스캔 %d / 삭제 %d (기한 %d일)',
                len(rows), deleted, days)
    return {'scanned': len(rows), 'deleted': deleted}


# ── 유틸 ──────────────────────────────────────────────────────
def _safe_rmtree(path: str) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
