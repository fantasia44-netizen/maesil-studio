"""image_analysis — 업로드한 데이터·성과 이미지를 Claude 비전으로 읽어
글 초안의 사실 근거(팩트) 텍스트로 변환한다.

원칙:
- 이미지에 **실제로 보이는 값만** 추출한다(숫자를 지어내지 않는다).
- 비율·평균 같은 계산은 하지 않는다(원본 값만) — 계산은 별도 단계(코드).
- 브랜드명·상품명·카테고리 등 업체 특정 정보는 익명 처리한다.

blog_generate 에서 manual_experience 와 동일한 방식으로 experience_block 에
주입해 E-E-A-T 근거로 쓴다. 비전 추출은 숫자 정확도가 중요해 Opus 5 를 쓴다.
"""
from __future__ import annotations

import base64
import logging

logger = logging.getLogger(__name__)

# 비전 추출: 숫자 정확도가 중요 → Opus 5 (편당 몇 번 안 부르니 품질 우선)
_VISION_MODEL = 'claude-opus-5'
_MAX_IMAGES = 6
_MAX_BYTES = 5 * 1024 * 1024   # 개별 이미지 상한(안전)
_ALLOWED_MIME = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')

_PROMPT = '''다음은 온라인 커머스 운영자가 올린 실제 성과·데이터 화면 캡처입니다.
이미지에 **실제로 보이는 것만** 사용해 한국어로 정리하세요. 없는 값을 지어내지 마세요.

[데이터]
- 표·숫자가 있으면 보이는 그대로 옮겨 적습니다(기간 / 지표 / 값). 표는 표 형태로.
- 비율·평균 같은 계산은 하지 마세요 — 화면에 보이는 원본 값만 옮깁니다.

[설명]
- 이 화면이 무엇을 보여주는지 1~3문장으로.

주의:
- 브랜드명·상품명·카테고리 등 업체를 특정할 수 있는 정보는 옮기지 마세요(익명 유지).
- 위 [데이터] / [설명] 형식의 담백한 텍스트만 출력하세요.'''


def _block(image_bytes: bytes, mime: str) -> dict | None:
    """이미지 바이트 → Claude image content block (필요시 압축)."""
    if not image_bytes:
        return None
    content, out_mime = image_bytes, (mime or 'image/jpeg')
    try:
        from services.image_utils import compress_image
        content, out_mime, _ext = compress_image(image_bytes, out_mime)
    except Exception as e:
        logger.debug('[image_analysis] 압축 생략(원본 사용): %s', e)
    if not content or len(content) > _MAX_BYTES:
        return None
    if out_mime not in _ALLOWED_MIME:
        out_mime = 'image/jpeg'
    return {
        'type': 'image',
        'source': {
            'type': 'base64',
            'media_type': out_mime,
            'data': base64.b64encode(content).decode('ascii'),
        },
    }


def analyze_images(images: list, anthropic_key: str) -> str:
    """images: [(bytes, mime), ...] → 프롬프트 주입용 근거 텍스트. 실패 시 ''.

    호출부는 실패해도 글 생성이 계속되도록 빈 문자열을 그대로 사용한다.
    """
    if not images or not anthropic_key:
        return ''
    blocks = []
    for data, mime in images[:_MAX_IMAGES]:
        blk = _block(data, mime)
        if blk:
            blocks.append(blk)
    if not blocks:
        return ''
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        msg = client.messages.create(
            model=_VISION_MODEL, max_tokens=1500,
            messages=[{'role': 'user',
                       'content': blocks + [{'type': 'text', 'text': _PROMPT}]}],
        )
        return (msg.content[0].text or '').strip()
    except Exception as e:
        logger.warning('[image_analysis] 비전 분석 실패: %s', e)
        return ''
