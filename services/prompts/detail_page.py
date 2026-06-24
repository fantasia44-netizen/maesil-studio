"""상세페이지 소구포인트 기획서 + 초안 제안서 프롬프트"""
from services.claude_service import build_brand_context

# ── 기획서 (소구포인트 분석) ─────────────────────────────────
_SYSTEM = """당신은 한국 온라인 커머스 전문 마케팅 전략가입니다.
소비자 심리와 구매 의사결정 흐름을 기반으로 상세페이지 시나리오를 기획하며,
디자인 업체에 그대로 넘길 수 있는 수준의 구체적인 기획서를 작성합니다.

중요: 결과는 순수 JSON만 출력하세요. 설명 문장, 마크다운 코드블록(```), 기타 텍스트 없이 JSON 객체만 출력합니다."""


def build_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    brand_ctx = build_brand_context(brand)
    product_name    = input_data.get('product_name',    '')
    features        = input_data.get('features',        '')
    target_customer = input_data.get('target_customer', '')
    differentiator  = input_data.get('differentiator',  '')
    price_range     = input_data.get('price_range',     '')

    system = f"""{_SYSTEM}

[브랜드 컨텍스트]
{brand_ctx}"""

    user = f"""아래 상품에 대한 상세페이지 기획서를 작성해 주세요.

상품명/카테고리: {product_name}
핵심 기능/특징: {features}
타겟 고객: {target_customer or '브랜드 프로필 기준'}
가격대: {price_range or '미입력'}
경쟁 대비 차별점: {differentiator or '미입력'}

[작성 기준]
1. 소비자가 상세페이지를 위에서 아래로 스크롤할 때 느끼는 감정·심리 흐름을 설계하세요.
2. 각 섹션은 "왜 이 순서여야 하는가"를 분명히 알 수 있도록 purpose를 작성하세요.
3. scene은 디자이너·촬영팀에게 전달하는 비주얼 디렉션입니다. 배경색/분위기/구도/소품/모델 사용 여부까지 구체적으로 쓰세요.
4. copy는 실제 상세페이지에 그대로 올릴 수 있는 카피를 쓰세요. 헤드라인+서브 또는 짧은 문단 형태로요.
5. 섹션 수는 6~8개로 구성하세요.

아래 JSON 형식으로만 출력하세요:

{{
  "appeal_analysis": {{
    "target_customer": "이 상품을 살 가능성이 가장 높은 고객 페르소나 (1~2줄, 구체적인 상황/나이대/라이프스타일 포함)",
    "core_pain": "이 고객이 실제로 느끼는 핵심 불편함 또는 욕구",
    "buy_trigger": "이 고객이 구매 버튼을 누르게 만드는 결정적 요인",
    "appeal_points": ["소구포인트1", "소구포인트2", "소구포인트3", "소구포인트4"]
  }},
  "sections": [
    {{
      "no": 1,
      "name": "섹션 이름 (예: 첫인상·훅)",
      "purpose": "이 섹션이 고객 심리에서 하는 역할 — 1문장",
      "scene": "디자이너 디렉션: 어떤 장면을 어떻게 만들어야 하는지 구체적으로 (배경, 분위기, 구도, 소품, 조명 등)",
      "copy": "이 섹션에 들어갈 실제 카피 (헤드라인+서브 또는 문단 형태)"
    }}
  ]
}}"""

    return system, user


# ── 초안 제안서 — 1개 타입씩 생성 (3번 호출) ───────────────
_PLAN_SYSTEM = """You are a senior Korean e-commerce marketing strategist.
Generate ONE detail page draft proposal with 6 sections.

CRITICAL OUTPUT RULES:
- Output ONLY valid JSON. No markdown, no explanation, no code fences.
- copy/name/purpose fields: Korean only.
- image_prompt fields: English only (for FLUX AI image generation).
- Keep all text SHORT to avoid truncation.
"""

_PLAN_TYPES = {
    '공감·문제해결형': 'Lead with customer pain empathy → problem cause → solution reveal → proof → benefit → CTA',
    '스토리·라이프스타일형': 'Brand/product story → aspiration lifestyle → product as enabler → testimonial → value → CTA',
    '데이터·전문가형': 'Hard data/stats → ingredient/tech proof → expert endorsement → social proof → value → CTA',
}


def build_single_plan_prompt(brand: dict, input_data: dict, type_name: str) -> tuple[str, str]:
    """1개 타입 상세페이지 초안 생성."""
    brand_ctx = build_brand_context(brand)
    strategy = _PLAN_TYPES.get(type_name, '')

    system = f"""{_PLAN_SYSTEM}

Brand context:
{brand_ctx}"""

    user = f"""Product: {input_data.get('product_name', '')}
Features: {input_data.get('features', '')}
Target: {input_data.get('target_customer') or 'brand profile default'}
Price: {input_data.get('price_range') or 'not specified'}
Differentiator: {input_data.get('differentiator') or 'not specified'}

Generate ONE plan with type_name "{type_name}".
Narrative strategy: {strategy}

6 sections required. Section names suggestion: 첫인상·훅 / 고객 공감 / 솔루션 소개 / 핵심 기능 / 신뢰 증거 / 구매 유도

Output ONLY this JSON (no other text):
{{
  "type_name": "{type_name}",
  "appeal_analysis": {{
    "target_customer": "Korean, max 30 chars",
    "core_pain": "Korean, max 30 chars",
    "buy_trigger": "Korean, max 30 chars",
    "appeal_points": ["point1", "point2", "point3"]
  }},
  "sections": [
    {{
      "no": 1,
      "name": "Korean, max 10 chars",
      "purpose": "Korean, max 40 chars",
      "copy": "Korean headline (max 20 chars)\\n supporting sentence (max 50 chars)",
      "image_prompt": "English, max 20 words, photographic, no text in image"
    }}
  ]
}}"""

    return system, user


def build_plan_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    """호환성 유지용 — 첫 번째 타입 프롬프트 반환."""
    return build_single_plan_prompt(brand, input_data, '공감·문제해결형')
