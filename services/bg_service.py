"""배경 제거 서비스
- 기본(10P): fal.ai BiRefNet Light — 빠르고 가벼움
- 고급(20P): fal.ai BiRefNet — 정밀 처리
모두 API 호출 방식 — 서버 메모리 무관
"""
import logging
import base64
import requests

logger = logging.getLogger(__name__)


def remove_bg_basic(image_bytes: bytes) -> bytes:
    """fal.ai BiRefNet Light — 10P, 빠른 배경 제거."""
    return _birefnet(image_bytes, model='General Use (Light)')


def remove_bg_advanced(image_bytes: bytes) -> bytes:
    """fal.ai BiRefNet — 20P, 정밀 배경 제거."""
    return _birefnet(image_bytes, model='General Use (Heavy)')


def _birefnet(image_bytes: bytes, model: str) -> bytes:
    from services.config_service import get_config
    api_key = get_config('fal_api_key')
    if not api_key:
        raise ValueError('fal_api_key가 설정되지 않았습니다.')

    b64 = base64.b64encode(image_bytes).decode()
    data_url = f'data:image/jpeg;base64,{b64}'

    resp = requests.post(
        'https://fal.run/fal-ai/birefnet',
        headers={
            'Authorization': f'Key {api_key}',
            'Content-Type': 'application/json',
        },
        json={'image_url': data_url, 'model': model},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    result_url = data.get('image', {}).get('url') or data.get('url', '')
    if not result_url:
        raise ValueError(f'BiRefNet 결과 없음: {data}')

    r = requests.get(result_url, timeout=30)
    r.raise_for_status()
    return r.content


def image_bytes_to_data_url(image_bytes: bytes, mime: str = 'image/png') -> str:
    b64 = base64.b64encode(image_bytes).decode()
    return f'data:{mime};base64,{b64}'
