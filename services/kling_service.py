"""Kling AI API 클라이언트 — image2video 파이프라인

공식 API: https://api.klingai.com
인증: JWT (HS256) — AccessKey ID + AccessKey Secret
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid

import requests

logger = logging.getLogger(__name__)

# ── 공식 Kling API 기본 URL ───────────────────────────────────
_BASE_URL = 'https://api.klingai.com'

# 씬 역할별 카메라 모션 프롬프트 (영문)
KLING_MOTION_BY_ROLE: dict[str, str] = {
    'hook':     'dramatic slow zoom in, eye-catching dynamic light pulse, suspenseful atmosphere',
    'empathy':  'gentle handheld camera sway, soft warm focus, intimate relatable mood',
    'solution': 'smooth confident forward push, product reveal focus, clean bright lighting',
    'benefit':  'upward rising camera motion, radiant glow expanding, energetic uplifting',
    'cta':      'elegant slow orbital rotation, sparkling highlights, inviting forward momentum',
    # 제품 리빌 전용 — 실제 제품 이미지가 입력될 때 사용
    'product_reveal': (
        'cinematic product reveal, dramatic glamour lighting sweep, '
        'slow elegant camera orbit, studio highlight glow, premium commercial feel, '
        'product stays sharp and centered, bokeh background'
    ),
}

_DEFAULT_MOTION = 'cinematic slow camera motion, smooth movement, professional commercial style'


# ════════════════════════════════════════════════════════════
# JWT 생성 (PyJWT 없이 표준 라이브러리만 사용)
# ════════════════════════════════════════════════════════════

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _gen_jwt(access_key: str, secret_key: str, expires_in: int = 1800) -> str:
    """Kling API JWT 토큰 생성 (HS256).

    Args:
        access_key: Kling 개발자 콘솔 AccessKey ID
        secret_key: Kling 개발자 콘솔 AccessKey Secret
        expires_in: 유효 시간(초), 기본 30분
    """
    now = int(time.time())
    header  = _b64url(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    payload = _b64url(json.dumps({
        'iss': access_key,
        'exp': now + expires_in,
        'nbf': now - 5,
    }, separators=(',', ':')).encode())

    signing_input = f'{header}.{payload}'
    sig = _b64url(
        hmac.new(secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    )
    return f'{signing_input}.{sig}'


def _headers(access_key: str, secret_key: str) -> dict:
    token = _gen_jwt(access_key, secret_key)
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }


# ════════════════════════════════════════════════════════════
# 연결 사전 확인 (포인트 차감 전 호출)
# ════════════════════════════════════════════════════════════

def verify_connection(
    access_key: str,
    secret_key: str,
    base_url: str = _BASE_URL,
    timeout: int = 8,
) -> tuple[bool, str]:
    """Kling API 연결 확인 — 실제 HTTP 요청으로 JWT 인증 검증.

    영상 생성 시작 전 호출해 포인트 차감 전에 연결 상태 확인.
    크레딧 소모 없음 (목록 GET 조회만).

    Returns:
        (ok: bool, message: str)
    """
    if not access_key or not secret_key:
        return False, 'Kling API 키가 설정되지 않았습니다. 관리자 설정에서 입력하세요.'
    try:
        url = f'{base_url.rstrip("/")}/v1/videos/image2video'
        resp = requests.get(
            url,
            headers=_headers(access_key, secret_key),
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get('code', -1) == 0:
                return True, 'Kling API 연결 성공'
            return False, f'API 응답 오류 (code={data.get("code")}): {data.get("message", "")}'
        elif resp.status_code == 401:
            return False, 'Kling 인증 실패 — Access Key / Secret Key를 확인하세요.'
        elif resp.status_code == 403:
            return False, 'Kling 권한 없음 — 개발자 API 플랜을 확인하세요.'
        else:
            return False, f'Kling API 응답 오류 (HTTP {resp.status_code})'
    except requests.Timeout:
        return False, 'Kling API 응답 시간 초과 (8초) — 네트워크를 확인하세요.'
    except Exception as e:
        return False, f'Kling API 연결 실패: {e}'


# ════════════════════════════════════════════════════════════
# 한글 → 영어 프롬프트 번역 (Kling은 영어 프롬프트만 정상 인식)
# ════════════════════════════════════════════════════════════

import re as _re
_KO_PATTERN = _re.compile(r'[가-힣ㄱ-ㅎㅏ-ㅣ]')


def ensure_english_prompt(prompt: str) -> str:
    """한글이 포함된 프롬프트를 영어로 번역 후 반환.

    이미 영어면 그대로 반환 (Claude 호출 없음).
    Kling은 영어 프롬프트만 정상 처리하므로 파이프라인 진입 전 반드시 호출.
    """
    if not prompt or not _KO_PATTERN.search(prompt):
        return prompt  # 한글 없음 → 번역 불필요

    logger.info('[kling] 한글 프롬프트 감지 → 영어 번역 중')
    try:
        from services.claude_service import generate_text
        system = (
            'You are a professional translator specializing in AI image/video generation prompts. '
            'Translate the given Korean text into English suitable for image generation. '
            'Output ONLY the translated English text. No explanations, no quotes.'
        )
        result = generate_text(
            system,
            f'Translate this to English for AI video generation:\n{prompt}',
            max_tokens=300,
            model='claude-haiku-4-5-20251001',
        )
        translated = result.strip()
        logger.info('[kling] 번역 완료: %s → %s', prompt[:40], translated[:40])
        return translated
    except Exception as e:
        logger.warning('[kling] 번역 실패 (원문 사용): %s', e)
        return prompt  # 번역 실패 시 원문 그대로 (최선)


# ════════════════════════════════════════════════════════════
# image2video API
# ════════════════════════════════════════════════════════════

def submit_image2video(
    image_url: str,
    scene_role: str,
    access_key: str,
    secret_key: str,
    model: str = 'kling-v1-6',
    duration: int = 5,
    cfg_scale: float = 0.5,
    base_url: str = _BASE_URL,
    max_retries: int = 4,
) -> str:
    """이미지 URL → Kling image2video 제출 → task_id 반환.

    429 Too Many Requests 시 지수 백오프 재시도 (최대 4회).

    Args:
        image_url: 공개 접근 가능한 이미지 URL (FLUX 생성 결과)
        scene_role: 씬 역할 (hook/empathy/solution/benefit/cta)
        model: kling-v1-6, kling-v2 등
        duration: 5 또는 10 (초)
        max_retries: 429 발생 시 최대 재시도 횟수
    Returns:
        task_id 문자열
    """
    motion_prompt = KLING_MOTION_BY_ROLE.get(scene_role, _DEFAULT_MOTION)
    full_prompt = f'{motion_prompt}, vertical 9:16 shorts format, no text, no watermark'

    payload = {
        'model_name': model,
        'image':      image_url,
        'prompt':     full_prompt,
        'negative_prompt': 'blur, distortion, low quality, watermark, text, logo, shaky',
        'cfg_scale':  cfg_scale,
        'mode':       'std',
        'duration':   str(duration),
        'aspect_ratio': '9:16',
    }

    url = f'{base_url.rstrip("/")}/v1/videos/image2video'

    for attempt in range(max_retries + 1):
        resp = requests.post(
            url,
            json=payload,
            headers=_headers(access_key, secret_key),
            timeout=30,
        )

        if resp.status_code == 429:
            # Retry-After 헤더 있으면 그 값, 없으면 지수 백오프 (30→60→120→240초)
            retry_after = int(resp.headers.get('Retry-After', 0))
            wait = retry_after if retry_after > 0 else min(30 * (2 ** attempt), 240)
            try:
                kling_msg = resp.json().get('message', '')
            except Exception:
                kling_msg = resp.text[:100]
            logger.warning('[kling] 429 rate limit (attempt %d/%d) msg=%s — %d초 대기',
                           attempt + 1, max_retries, kling_msg, wait)
            if attempt < max_retries:
                time.sleep(wait)
                continue
            else:
                raise RuntimeError(
                    f'Kling API 요청 한도 초과 — {max_retries}회 재시도 모두 실패.\n'
                    f'Kling 응답: {kling_msg}\n'
                    '5~10분 후 다시 시도하거나 Kling 콘솔에서 플랜/한도를 확인해주세요.'
                )

        resp.raise_for_status()
        data = resp.json()

        code = data.get('code', -1)
        if code != 0:
            raise RuntimeError(f'Kling API 오류 (code={code}): {data.get("message", data)}')

        task_id = data.get('data', {}).get('task_id', '')
        if not task_id:
            raise RuntimeError(f'task_id 없음: {data}')
        logger.info('[kling] 제출 완료: task_id=%s scene_role=%s (attempt %d)',
                    task_id, scene_role, attempt + 1)
        return task_id

    raise RuntimeError('Kling 제출 실패 — 재시도 횟수 초과')


# ════════════════════════════════════════════════════════════
# 상태 조회 & 폴링
# ════════════════════════════════════════════════════════════

def get_task_status(
    task_id: str,
    access_key: str,
    secret_key: str,
    base_url: str = _BASE_URL,
) -> dict:
    """task_id → {'status': str, 'video_url': str|None, 'error': str|None}.

    status: 'submitted' | 'processing' | 'succeed' | 'failed'
    """
    url = f'{base_url.rstrip("/")}/v1/videos/image2video/{task_id}'
    resp = requests.get(url, headers=_headers(access_key, secret_key), timeout=20)
    resp.raise_for_status()
    data = resp.json()

    code = data.get('code', -1)
    if code != 0:
        return {'status': 'failed', 'video_url': None, 'error': data.get('message', str(data))}

    task_data   = data.get('data', {})
    task_status = task_data.get('task_status', 'processing')
    video_url   = None

    if task_status == 'succeed':
        videos = task_data.get('task_result', {}).get('videos', [])
        if videos:
            video_url = videos[0].get('url')

    return {
        'status':    task_status,
        'video_url': video_url,
        'error':     task_data.get('task_status_msg') if task_status == 'failed' else None,
    }


def wait_for_task(
    task_id: str,
    access_key: str,
    secret_key: str,
    base_url: str = _BASE_URL,
    timeout: int = 900,    # 15분
    poll_interval: int = 10,
    on_progress=None,      # callable(elapsed_sec) — 진행 콜백
) -> str:
    """폴링 루프 — 완료 시 video_url 반환, 실패/타임아웃 시 예외.

    Args:
        on_progress: 매 폴 때 호출되는 콜백 (None 가능)
    """
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            raise TimeoutError(f'Kling 작업 타임아웃 ({timeout}초 초과): task_id={task_id}')

        result = get_task_status(task_id, access_key, secret_key, base_url)
        status = result['status']

        if status == 'succeed':
            video_url = result.get('video_url')
            if not video_url:
                raise RuntimeError(f'Kling 작업 완료 but video_url 없음: {result}')
            logger.info('[kling] 완료: task_id=%s elapsed=%.0fs', task_id, elapsed)
            return video_url

        if status == 'failed':
            raise RuntimeError(f'Kling 작업 실패: {result.get("error")} (task_id={task_id})')

        if on_progress:
            try:
                on_progress(elapsed)
            except Exception:
                pass

        logger.debug('[kling] 폴링: task_id=%s status=%s elapsed=%.0fs', task_id, status, elapsed)
        time.sleep(poll_interval)


# ════════════════════════════════════════════════════════════
# 영상 다운로드
# ════════════════════════════════════════════════════════════

def download_video(url: str, dest_path: str, timeout: int = 120) -> str:
    """Kling CDN URL → 로컬 파일 저장 → dest_path 반환.

    Kling 영상 URL은 24시간 유효 → 즉시 저장 필요.
    """
    resp = requests.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()
    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    logger.info('[kling] 영상 다운로드 완료: %s (%d bytes)', dest_path,
                int(resp.headers.get('content-length', 0)))
    return dest_path
