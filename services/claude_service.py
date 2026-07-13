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

    브랜드 기본 정보 + 회사 정보(연혁·성과·거래처 등)를 모두 포함해
    Claude 가 풍부한 배경 지식을 갖고 고품질 콘텐츠를 생성하도록 한다.

    Args:
      brand: brand_profiles row.
      product: 선택. products row — 상품 기반 생성 시 컨텍스트에 추가 주입.
      merged_avoid_words: 3-tier 합집합 금지어.
                          None 이면 brand.avoid_words 만 표시 (이전 호환).
    """
    parts = []

    # ── 1. 브랜드 기본 ────────────────────────────────────────
    parts.append('[브랜드 기본 정보]')
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
        parts.append(f'- 브랜드 스토리/추가 정보: {brand["extra_context"]}')

    # ── 2. 회사 정보 (있을 때만) ─────────────────────────────
    company_fields = []
    if brand.get('founded_year'):
        company_fields.append(f'- 창업 연도: {brand["founded_year"]}년')
    if brand.get('ceo_name'):
        company_fields.append(f'- 대표자: {brand["ceo_name"]}')
    if brand.get('employee_count'):
        company_fields.append(f'- 직원 규모: {brand["employee_count"]}')
    if brand.get('website'):
        company_fields.append(f'- 홈페이지: {brand["website"]}')
    if brand.get('address'):
        company_fields.append(f'- 소재지: {brand["address"]}')
    if brand.get('contact_phone'):
        company_fields.append(f'- 연락처: {brand["contact_phone"]}')
    if brand.get('contact_email'):
        company_fields.append(f'- 이메일: {brand["contact_email"]}')
    if company_fields:
        parts.append('')
        parts.append('[회사 정보]')
        parts.extend(company_fields)

    # ── 3. 회사 이력·성과·신뢰도 (있을 때만) ─────────────────
    cred_fields = []
    if brand.get('certifications'):
        cred_fields.append(f'- 인증·수상 이력: {brand["certifications"]}')
    if brand.get('key_stats'):
        cred_fields.append(f'- 핵심 성과 수치: {brand["key_stats"]}')
    if brand.get('references_text'):
        cred_fields.append(f'- 주요 거래처·납품처: {brand["references_text"]}')
    if cred_fields:
        parts.append('')
        parts.append('[신뢰도·성과 지표]')
        parts.extend(cred_fields)

    # ── 4. 상품 컨텍스트 (있을 때) ──────────────────────────
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
        if product.get('description'):
            parts.append(f'- 상품 설명: {product["description"]}')
        feats = product.get('features')
        if feats:
            feats_list = feats if isinstance(feats, list) else [feats]
            if feats_list:
                parts.append('- 핵심 특징:')
                for f in feats_list:
                    parts.append(f'  · {f}')

    # ── 5. 금지 표현 ─────────────────────────────────────────
    if merged_avoid_words is None:
        if brand.get('avoid_words'):
            avoids = brand['avoid_words'] if isinstance(brand['avoid_words'], list) else [brand['avoid_words']]
            merged_avoid_words = list(avoids)
    if merged_avoid_words:
        parts.append('')
        parts.append('[절대 사용 금지 표현 — 위반 시 광고법 등 법적 리스크]')
        parts.append('다음 표현은 본문/제목/태그/메타에 절대 사용하지 마시오:')
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


def generate_with_images(system_prompt: str, user_prompt: str,
                         images: list, max_tokens: int = 4096,
                         model: str = None) -> str:
    """다중 이미지 + 텍스트 → 텍스트 생성 (Claude Vision).

    images: [(b64_data, media_type), ...] — base64 인코딩된 이미지 목록 (순서 유지).
    경험담 블로그 등 '사진을 보고 글을 쓰는' 작업용.
    """
    client = get_client()
    content = []
    for i, (b64, mt) in enumerate(images, 1):
        content.append({'type': 'text', 'text': f'[사진 {i}]'})
        content.append({
            'type': 'image',
            'source': {'type': 'base64', 'media_type': mt or 'image/jpeg', 'data': b64},
        })
    content.append({'type': 'text', 'text': user_prompt})
    message = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{'role': 'user', 'content': content}],
    )
    return message.content[0].text


def analyze_product_image(image_bytes: bytes, media_type: str = 'image/jpeg') -> dict:
    """Claude Vision으로 상품 이미지 분석 → 상품 정보 JSON 반환.

    반환 형식:
        {name, category, price(int|None), features(list[str]), description}
    """
    import base64, json, re
    client = get_client()
    img_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')

    message = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=800,
        system=(
            '당신은 상품 이미지를 분석해 상품 정보를 추출하는 전문가입니다. '
            '이미지를 보고 상품명, 카테고리, 가격, 핵심 특징, 설명을 추출하여 '
            '반드시 순수 JSON만 출력하세요(코드블록·설명 없이).'
        ),
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': media_type,
                        'data': img_b64,
                    },
                },
                {
                    'type': 'text',
                    'text': (
                        '이 상품 이미지를 분석하고 아래 JSON 형식으로만 응답하세요:\n'
                        '{"name":"상품명","category":"카테고리",'
                        '"price":숫자또는null,'
                        '"features":["특징1","특징2","특징3"],'
                        '"description":"상품 설명 1~2문장"}\n\n'
                        '규칙:\n'
                        '- 이미지에 텍스트·로고·패키지가 있으면 적극 활용\n'
                        '- 가격이 보이지 않으면 null\n'
                        '- features 3~5개, 각 10~30자\n'
                        '- 순수 JSON만 출력'
                    ),
                },
            ],
        }],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE).strip()
    return json.loads(raw)
