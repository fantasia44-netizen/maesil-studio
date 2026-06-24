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
경쟁 차별점: {differentiator or '미입력'}"""

    return system, user


# ── Phase 1: 3타입 미리보기 (섹션명만, 카피 없음) ────────────
_PREVIEW_SYSTEM = """You are a Korean e-commerce marketing strategist.
Output ONLY valid JSON. No markdown, no explanation.
Korean for all fields except type_name keys."""

_PLAN_TYPES = ['공감·문제해결형', '스토리·라이프스타일형', '데이터·전문가형']


def build_preview_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    """3타입 미리보기 — 섹션 이름 목록 + 소구 요약만 생성. 빠르고 작음."""
    brand_ctx = build_brand_context(brand)

    system = f"{_PREVIEW_SYSTEM}\n\nBrand: {brand_ctx}"

    user = f"""Product: {input_data.get('product_name','')}
Features: {input_data.get('features','')}
Target: {input_data.get('target_customer') or 'brand default'}
Price: {input_data.get('price_range') or '-'}
Differentiator: {input_data.get('differentiator') or '-'}

Generate previews for 3 plan types. Each has 6 section NAMES only (no copy).

Output ONLY this JSON:
{{
  "plans": [
    {{
      "type_name": "공감·문제해결형",
      "hook": "이 타입의 핵심 전략을 한 줄로 (Korean, max 40자)",
      "appeal_analysis": {{
        "target_customer": "Korean, max 25자",
        "core_pain": "Korean, max 25자",
        "buy_trigger": "Korean, max 25자",
        "appeal_points": ["point1", "point2", "point3"]
      }},
      "sections": [
        {{"no": 1, "name": "Korean section name, max 8자", "purpose": "Korean, max 30자"}},
        {{"no": 2, "name": "...", "purpose": "..."}},
        {{"no": 3, "name": "...", "purpose": "..."}},
        {{"no": 4, "name": "...", "purpose": "..."}},
        {{"no": 5, "name": "...", "purpose": "..."}},
        {{"no": 6, "name": "...", "purpose": "..."}}
      ]
    }},
    {{
      "type_name": "스토리·라이프스타일형",
      "hook": "...",
      "appeal_analysis": {{ ... }},
      "sections": [ ... 6 sections ... ]
    }},
    {{
      "type_name": "데이터·전문가형",
      "hook": "...",
      "appeal_analysis": {{ ... }},
      "sections": [ ... 6 sections ... ]
    }}
  ]
}}"""

    return system, user


# ── Phase 2: 선택된 1타입 카피 생성 ─────────────────────────
def build_copy_prompt(brand: dict, input_data: dict, plan_preview: dict) -> tuple[str, str]:
    """선택된 플랜의 섹션별 카피 생성. 이미지 프롬프트 없음."""
    brand_ctx = build_brand_context(brand)
    type_name = plan_preview.get('type_name', '')
    hook      = plan_preview.get('hook', '')
    sections  = plan_preview.get('sections', [])
    sec_list  = '\n'.join(f"{s['no']}. {s['name']} — {s.get('purpose','')}" for s in sections)

    system = f"""당신은 한국 온라인 커머스 카피라이터입니다.
결과는 순수 JSON만 출력합니다. 마크다운 없음.

브랜드: {brand_ctx}"""

    user = f"""상품: {input_data.get('product_name','')}
특징: {input_data.get('features','')}
타겟: {input_data.get('target_customer') or '브랜드 기준'}
가격: {input_data.get('price_range') or '-'}
차별점: {input_data.get('differentiator') or '-'}

기획 방향: {type_name} — {hook}

아래 6개 섹션의 카피를 작성하세요:
{sec_list}

각 섹션 카피 규칙:
- 헤드라인: 15자 이내 (임팩트 있는 짧은 문구)
- 본문: 2~3줄, 각 줄 30자 이내
- 이모지나 특수기호 없이 순수 텍스트

Output ONLY this JSON:
{{
  "copies": [
    {{"no": 1, "copy": "헤드라인\\n본문 첫줄\\n본문 둘째줄"}},
    {{"no": 2, "copy": "..."}},
    {{"no": 3, "copy": "..."}},
    {{"no": 4, "copy": "..."}},
    {{"no": 5, "copy": "..."}},
    {{"no": 6, "copy": "..."}}
  ]
}}"""

    return system, user


# ── Phase 3: 섹션별 이미지 프롬프트 생성 ────────────────────
def build_image_prompt_for_section(section: dict, product_name: str) -> str:
    """섹션 정보로 FLUX용 영문 이미지 프롬프트 생성 (규칙 기반, API 호출 없음)."""
    name    = section.get('name', '')
    purpose = section.get('purpose', '')
    copy    = (section.get('copy') or '').split('\n')[0][:30]  # 헤드라인만

    # 섹션 이름 → 장면 패턴 매핑
    scene_map = {
        '첫인상': 'hero product shot, dramatic lighting, clean white background, premium feel',
        '훅':     'striking close-up product detail, macro photography, shallow depth of field',
        '공감':   'person looking concerned or thoughtful, soft natural light, lifestyle photo',
        '문제':   'before scenario, muted colors, person experiencing discomfort, documentary style',
        '솔루션': 'product in use, bright clean environment, hands interacting with product',
        '소개':   'product reveal shot, elegant styling, studio lighting, premium composition',
        '기능':   'product feature close-up, technical beauty shot, clean background, sharp focus',
        '성분':   'ingredient flat lay, natural materials, marble surface, overhead shot',
        '증거':   'before and after split composition, clinical clean aesthetic, data visualization style',
        '신뢰':   'award or certification display, professional setting, credible clean design',
        '후기':   'happy customer lifestyle photo, natural light, genuine smile, product in hand',
        '구매':   'product packaging beauty shot, gift-ready styling, warm inviting light',
        '라이프': 'aspirational lifestyle scene, bright airy environment, product naturally placed',
        '스토리': 'behind the scenes craftsmanship, warm natural light, authentic documentary feel',
        '데이터': 'scientific lab aesthetic, clean white environment, precise technical photography',
    }

    scene = 'professional product photography, clean background, commercial style'
    for key, val in scene_map.items():
        if key in name or key in purpose:
            scene = val
            break

    return f"{product_name} product, {scene}, high resolution, no text no words no letters"
