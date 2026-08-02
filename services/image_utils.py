"""이미지 압축·리사이즈 유틸 — 업로드/저장 전 저용량 변환.

AI가 생성한 거대 PNG(수백 KB~수 MB)를 리사이즈 + WebP로 재인코딩해
전송량과 LCP(페이지 로드 속도)를 줄인다.

어떤 이유로든 실패하면 원본 bytes를 그대로 반환해 업로드 흐름을 절대 깨지 않는다.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

_MAX_DIM = 1600          # 가로/세로 최대 px (초과 시 비율 유지 축소)
_WEBP_QUALITY = 82       # WebP 품질 (82 ≈ 육안 무손실 수준)
_SKIP_UNDER_BYTES = 60 * 1024   # 이미 작으면(아이콘/로고 등) 건드리지 않음


def _ext_from_mime(mime: str) -> str:
    m = (mime or '').lower()
    if 'png' in m:
        return 'png'
    if 'webp' in m:
        return 'webp'
    if 'gif' in m:
        return 'gif'
    return 'jpg'


def compress_image(data: bytes, mime: str = '', *,
                   max_dim: int = _MAX_DIM,
                   quality: int = _WEBP_QUALITY) -> tuple[bytes, str, str]:
    """(bytes, mime) → (압축 bytes, mime, ext).

    - GIF/SVG/애니메이션·이미 작은 이미지는 원본 그대로 반환
    - max_dim 초과 시 비율 유지 리사이즈
    - WebP(quality)로 재인코딩(투명도 보존). 변환이 더 커지면 원본 유지
    - 실패 시 원본 반환 (호출 흐름 보호)
    """
    try:
        if 'gif' in (mime or '') or 'svg' in (mime or ''):
            return data, mime, _ext_from_mime(mime)
        if len(data) <= _SKIP_UNDER_BYTES:
            return data, mime, _ext_from_mime(mime)

        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if getattr(img, 'is_animated', False):
            return data, mime, _ext_from_mime(mime)

        w, h = img.size
        if max(w, h) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        img = img.convert('RGBA') if img.mode in ('RGBA', 'LA', 'P') else img.convert('RGB')

        buf = io.BytesIO()
        img.save(buf, format='WEBP', quality=quality, method=6)
        out = buf.getvalue()

        if out and len(out) < len(data):
            logger.info('[image_utils] %dKB → %dKB (WebP)', len(data) // 1024, len(out) // 1024)
            return out, 'image/webp', 'webp'
        return data, mime, _ext_from_mime(mime)
    except Exception as e:
        logger.warning('[image_utils] 압축 실패 — 원본 사용: %s', e)
        return data, mime, _ext_from_mime(mime)
