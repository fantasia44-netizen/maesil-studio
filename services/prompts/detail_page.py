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
    """선택된 플랜의 섹션별 카피 + 영문 이미지 프롬프트 동시 생성."""
    brand_ctx    = build_brand_context(brand)
    type_name    = plan_preview.get('type_name', '')
    hook         = plan_preview.get('hook', '')
    sections     = plan_preview.get('sections', [])
    appeal       = plan_preview.get('appeal_analysis', {})
    sec_list     = '\n'.join(
        f"{s['no']}. [{s['name']}] 목적: {s.get('purpose','')}" for s in sections
    )

    product_name = input_data.get('product_name', '')
    features     = input_data.get('features', '')
    target       = input_data.get('target_customer') or appeal.get('target_customer', '브랜드 기준')
    diff         = input_data.get('differentiator') or ''
    price        = input_data.get('price_range') or ''

    # 타입별 카피 전략 힌트
    strategy_hint = {
        '공감·문제해결형': (
            "독자가 '맞아, 나 얘기다'라고 느끼게. "
            "섹션 흐름: 고통 공감 → 문제 제기 → 해결책 → 증거 → 안심 → 구매 유도. "
            "자극적이지 않고 따뜻한 어조. 숫자/사실보다 감정 언어 우선."
        ),
        '스토리·라이프스타일형': (
            "브랜드 철학과 라이프스타일 이미지 중심. "
            "섹션 흐름: 세계관 제시 → 주인공 이야기 → 제품 등장 → 경험 → 변화 → 초대. "
            "시적이고 감성적인 문장. 짧은 문장 여러 개가 효과적."
        ),
        '데이터·전문가형': (
            "수치·인증·전문성으로 신뢰 구축. "
            "섹션 흐름: 임팩트 수치 → 문제 정의 → 전문가 솔루션 → 성분/기술 → 실증 → 결론. "
            "구체적 숫자, 성분명, 수상 실적 등 팩트 적극 활용."
        ),
    }.get(type_name, '설득력 있는 카피라이팅')

    appeal_ctx = ''
    if appeal:
        pain    = appeal.get('core_pain', '')
        trigger = appeal.get('buy_trigger', '')
        points  = ', '.join(appeal.get('appeal_points', []))
        appeal_ctx = f"""
타겟 핵심 고통: {pain}
구매 결정 트리거: {trigger}
소구 포인트: {points}"""

    system = f"""당신은 대한민국 최고 수준의 온라인 커머스 카피라이터입니다.
쿠팡·네이버 상세페이지 판매 전환율 최적화 전문가입니다.
결과는 순수 JSON만 출력합니다. 마크다운 없음.

브랜드: {brand_ctx}"""

    user = f"""[상품 정보]
상품명: {product_name}
핵심 특징: {features}
타겟 고객: {target}
가격대: {price or '-'}
경쟁 차별점: {diff or '-'}
{appeal_ctx}

[기획 방향]
타입: {type_name}
핵심 전략: {hook}
카피 작성 원칙: {strategy_hint}

[섹션 목록]
{sec_list}

[카피 작성 규칙]
1. 헤드라인(첫 줄): 10자 이내, 핵심 감정/가치를 담은 임팩트 문구
2. 본문(2~3줄): 각 줄 25자 이내, 설득력 있는 구체적 문장
3. 각 섹션은 해당 목적에 충실하게, 전체 흐름이 자연스럽게 이어져야 함
4. 진부한 표현 금지: "최고", "최상", "품질 좋은" 같은 막연한 단어 배제
5. 이모지·특수기호 없이 순수 텍스트만
6. 타겟 고객의 언어로 — 그들이 실제 쓰는 표현, 그들이 느끼는 감정

[image_prompt 규칙 — FLUX AI 전송용]
- 반드시 영어로만 작성 (한글 절대 금지)
- {product_name} 실제 제품이 화면 중심에 있는 상세페이지 장면
- 섹션 목적과 카피 분위기에 어울리는 촬영 스타일
- 20단어 이내, 구체적 촬영 설정 묘사
- no text, no words, no letters in the image

Output ONLY this JSON:
{{
  "copies": [
    {{"no": 1, "copy": "헤드라인\\n본문 첫줄\\n본문 둘째줄", "image_prompt": "English scene"}},
    {{"no": 2, "copy": "...", "image_prompt": "..."}},
    {{"no": 3, "copy": "...", "image_prompt": "..."}},
    {{"no": 4, "copy": "...", "image_prompt": "..."}},
    {{"no": 5, "copy": "...", "image_prompt": "..."}},
    {{"no": 6, "copy": "...", "image_prompt": "..."}}
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

    return f"{scene}, high resolution, no text no words no letters in the image"
