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


def build_brand_context(brand: dict,
                        product: dict | None = None,
                        merged_avoid_words: list[str] | None = None) -> str:
    """브랜드(+선택적 상품) → 시스템 프롬프트 컨텍스트.

    Args:
      brand: brand_profiles row.
      product: 선택. products row — 상품 기반 생성 시 컨텍스트에 추가 주입.
      merged_avoid_words: 3-tier 합집합 금지어 (services.regulatory.combine_avoid_words).
                          None 이면 brand.avoid_words 만 표시 (이전 호환).
    """
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
    if brand.get('extra_context'):
        parts.append(f'- 브랜드 추가 정보: {brand["extra_context"]}')

    # ── 상품 컨텍스트 (있을 때) ──────────────────────────────
    if product:
        parts.append('')
        parts.append('[상품 정보]')
        if product.get('name'):
            parts.append(f'- 상품명: {product["name"]}')
        if product.get('category'):
            parts.append(f'- 카테고리: {product["category"]}')
        if product.get('price'):
            try:
                parts.append(f'- 가격: {int(product["price"]):,}원')
            except (TypeError, ValueError):
                pass
        feats = product.get('features')
        if feats:
            feats_list = feats if isinstance(feats, list) else [feats]
            if feats_list:
                parts.append('- 핵심 특징:')
                for f in feats_list:
                    parts.append(f'  · {f}')

    # ── 금지 표현 (3-tier 합집합 우선, 없으면 브랜드만) ──────
    if merged_avoid_words is None:
        if brand.get('avoid_words'):
            avoids = brand['avoid_words'] if isinstance(brand['avoid_words'], list) else [brand['avoid_words']]
            merged_avoid_words = list(avoids)
    if merged_avoid_words:
        parts.append('')
        parts.append('[절대 사용 금지 표현 — 위반 시 광고법 등 법적 리스크]')
        parts.append(f'다음 표현은 본문/제목/태그/메타에 절대 사용하지 마시오:')
        parts.append(f'  {", ".join(merged_avoid_words)}')

    return '\n'.join(parts) if parts else '브랜드 정보 없음'


def generate_text(system_prompt: str, user_prompt: str,
                  max_tokens: int = 4096, model: str = None) -> str:
    """텍스트 생성 — 결과 문자열 반환.
    model 미지정 시 DEFAULT_MODEL(Sonnet) 사용.
    저렴한 단순 작업엔 'claude-haiku-4-5-20251001' 지정 가능.
    """
    client = get_client()
    message = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{'role': 'user', 'content': user_prompt}],
    )
    return message.content[0].text
