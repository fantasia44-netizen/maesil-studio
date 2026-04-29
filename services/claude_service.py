"""Claude API 래퍼"""
import os
import logging
from anthropic import Anthropic

logger = logging.getLogger(__name__)

_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        from services.config_service import get_config
        _client = Anthropic(api_key=get_config('anthropic_api_key'))
    return _client


DEFAULT_MODEL = 'claude-sonnet-4-6'
SYSTEM_BASE = (
    '당신은 한국 온라인 커머스 전문 마케터입니다. '
    '브랜드 정보를 바탕으로 채널에 최적화된 콘텐츠를 생성합니다. '
    '결과물은 한국어로 작성하며, 구체적이고 실용적인 내용을 담습니다.'
)


def build_brand_context(brand: dict) -> str:
    """brand_profile dict → 시스템 프롬프트 컨텍스트"""
    parts = []
    if brand.get('name'):
        parts.append(f'- 브랜드명: {brand["name"]}')
    if brand.get('industry'):
        parts.append(f'- 업종: {brand["industry"]}')
    if brand.get('target_customer'):
        parts.append(f'- 타겟 고객: {brand["target_customer"]}')
    if brand.get('brand_tone'):
        tones = brand['brand_tone'] if isinstance(brand['brand_tone'], list) else [brand['brand_tone']]
        parts.append(f'- 톤앤매너: {", ".join(tones)}')
    if brand.get('keywords'):
        kws = brand['keywords'] if isinstance(brand['keywords'], list) else [brand['keywords']]
        parts.append(f'- 핵심 키워드: {", ".join(kws)}')
    if brand.get('avoid_words'):
        avoids = brand['avoid_words'] if isinstance(brand['avoid_words'], list) else [brand['avoid_words']]
        parts.append(f'- 금지 표현: {", ".join(avoids)}')
    if brand.get('extra_context'):
        parts.append(f'- 추가 정보: {brand["extra_context"]}')
    return '\n'.join(parts) if parts else '브랜드 정보 없음'


def generate_text(system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
    """텍스트 생성 — 결과 문자열 반환"""
    client = get_client()
    message = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_prompt}],
    )
    return message.content[0].text
