"""배경 제거 서비스
- 기본: rembg (Python, 무료)
- 고급: fal.ai BiRefNet (포인트 20P)
"""
import logging
import base64
import requests
from io import BytesIO

logger = logging.getLogger(__name__)


def remove_bg_basic(image_bytes: bytes) -> bytes:
    """rembg — 무료 배경 제거. PNG with alpha 반환."""
    try:
        from rembg import remove
        result = remove(image_bytes)
        return result
    except Exception as e:
        logger.error(f'[BG] rembg error: {e}')
        raise ValueError(f'배경 제거 실패: {e}')


def remove_bg_advanced(image_bytes: bytes) -> bytes:
    """fal.ai BiRefNet — 고급 배경 제거. PNG with alpha 반환."""
    from services.config_service import get_config
    api_key = get_config('fal_api_key')
    if not api_key:
        raise ValueError('fal_api_key가 설정되지 않았습니다.')

    # base64로 변환해서 전송
    b64 = base64.b64encode(image_bytes).decode()
    data_url = f'data:image/jpeg;base64,{b64}'

    resp = requests.post(
        'https://fal.run/fal-ai/birefnet',
        headers={
            'Authorization': f'Key {api_key}',
            'Content-Type': 'application/json',
        },
        json={'image_url': data_url, 'model': 'General Use (Light)'},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    # 결과 이미지 다운로드
    result_url = data.get('image', {}).get('url') or data.get('url', '')
    if not result_url:
        raise ValueError(f'BiRefNet 결과 없음: {data}')

    r = requests.get(result_url, timeout=30)
    r.raise_for_status()
    return r.content


def image_bytes_to_data_url(image_bytes: bytes, mime: str = 'image/png') -> str:
    b64 = base64.b64encode(image_bytes).decode()
    return f'data:{mime};base64,{b64}'


def data_url_to_bytes(data_url: str) -> tuple[bytes, str]:
    """data URL → (bytes, mime_type)"""
    header, b64data = data_url.split(',', 1)
    mime = header.split(';')[0].split(':')[1]
    return base64.b64decode(b64data), mime
