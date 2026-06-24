"""상세페이지 소구포인트 기획서 + 초안 제안서 프롬프트"""
from services.claude_service import build_brand_context


def build_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    """레거시 호환용 — 현재는 build_preview_prompt + build_copy_prompt 사용."""
    return build_preview_prompt(brand, input_data)


# ── Phase 1: 3타입 미리보기 (섹션 구조만, 카피 없음) ──────────
_PREVIEW_SYSTEM = """당신은 대한민국 최고 수준의 온라인 커머스 마케팅 전략가입니다.
쿠팡·네이버스마트스토어 전환율 최적화 전문가로서,
고객 심리 기반의 상세페이지 시나리오를 설계합니다.

핵심 원칙:
- 상세페이지는 '우리 제품이 좋다'가 아니라 '고객의 고민을 우리가 해결한다'가 중심
- 고객은 스크롤하며 자신의 이야기를 찾는다 — 첫 3초에 공감을 얻어야 함
- 각 섹션은 구매 심리 여정의 한 단계: 공감 → 문제인식 → 해결책 → 신뢰 → 결심
- 결과는 순수 JSON만 출력. 마크다운 없음."""

_TYPE_STRATEGIES = {
    '공감·문제해결형': """
[전략] PAS 프레임워크 (Problem → Agitation → Solution)
- 고객이 느끼는 불편/고통/실망을 먼저 말해줌으로써 "나를 아는 브랜드"라는 신뢰 형성
- 섹션 심리 여정:
  1) 공감 훅: "혹시 이런 경험 있으신가요?" — 고객 상황을 구체적으로 묘사
  2) 문제 심화: 그 불편이 왜 계속되는지, 기존 해결책이 왜 실패하는지
  3) 전환점/해결책: 이 제품이 어떻게 다른 방식으로 해결하는지
  4) 작동 원리/특징: 왜 효과적인지 납득 근거 제시
  5) 사회적 증거: "나만 겪는 문제가 아니었구나" + 해결된 사람들의 이야기
  6) 결심 유도: 리스크 제거 + 지금 선택해야 하는 이유""",

    '스토리·라이프스타일형': """
[전략] BAB 프레임워크 (Before → After → Bridge)
- 제품을 파는 게 아니라 '더 나은 삶의 모습'을 파는 방식
- 고객이 원하는 라이프스타일을 먼저 제시하고, 제품은 그 다리(Bridge)로 등장
- 섹션 심리 여정:
  1) 세계관/비전: 고객이 꿈꾸는 삶의 장면 — 감성적 화면 제시
  2) 지금 현실: 하지만 현재는 그렇지 못한 이유 (갈망 자극)
  3) 브랜드 철학: 우리가 이 갭을 어떻게 바라보는가 — 가치관 공명
  4) 제품/경험: 이 제품을 통해 달라지는 일상 장면
  5) 사람들의 변화: 먼저 경험한 사람들의 Before/After
  6) 함께하는 초대: "당신의 이야기를 시작하세요" — 커뮤니티/브랜드 참여""",

    '데이터·전문가형': """
[전략] ACCA 프레임워크 (Awareness → Comprehension → Conviction → Action)
- 수치·성분·인증·비교로 이성적 납득을 이끌어 신뢰 구축
- "전문가도 인정한, 데이터로 증명된" 포지셔닝
- 섹션 심리 여정:
  1) 임팩트 수치: 첫 화면에 놀라운 숫자/사실 — 스크롤 멈추게 하기
  2) 문제 정의: 시장/카테고리의 현실적 문제를 데이터로 제시
  3) 기술/성분 차별화: 경쟁 제품과 구체적으로 무엇이 다른가
  4) 전문가 검증: 성분 안전성, 임상, 수상, 미디어 언급
  5) 사용 결과 데이터: 실제 사용자 비교 수치, 리뷰 통계
  6) 스펙 정리 + CTA: 한눈에 보는 구성/가격/보증""",
}


def build_preview_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    """3타입 미리보기 — 섹션 구조 + 소구 전략 분석. 카피 없음."""
    brand_ctx    = build_brand_context(brand)
    product_name = input_data.get('product_name', '')
    features     = input_data.get('features', '')
    target       = input_data.get('target_customer') or ''
    diff         = input_data.get('differentiator') or ''
    price        = input_data.get('price_range') or ''

    type_blocks = '\n'.join(
        f'타입명: "{t}"\n전략:{s}' for t, s in _TYPE_STRATEGIES.items()
    )

    system = f"""{_PREVIEW_SYSTEM}

[브랜드 정보]
{brand_ctx}"""

    user = f"""아래 상품에 대해 3가지 상세페이지 타입의 섹션 구조를 설계해 주세요.

[상품 정보]
상품명: {product_name}
핵심 특징: {features}
타겟 고객: {target or '브랜드 프로필 기준'}
가격대: {price or '-'}
경쟁 차별점: {diff or '-'}

[3가지 타입과 전략]
{type_blocks}

[설계 원칙]
- 각 섹션명은 '기능명'이 아니라 '고객이 그 섹션에서 느껴야 할 감정/상황'을 반영
  - 나쁜 예: "제품 소개", "특징 설명", "구성품"
  - 좋은 예: "아직도 이걸 반복하세요?", "처음 느낀 그 순간", "엄마들이 먼저 알아본 이유"
- appeal_analysis는 이 상품을 이 타겟에게 팔기 위한 핵심 전략 분석
  - core_pain: 고객이 매일 겪는 구체적 불편/고통 (추상적 표현 금지)
  - buy_trigger: 구매 결정 순간에 작동하는 심리 (예: "내 아이만큼은 다르게", "더 이상 돈 낭비 싫다")
  - appeal_points: 이 타입에서 강조할 소구 포인트 3가지

Output ONLY this JSON:
{{
  "plans": [
    {{
      "type_name": "공감·문제해결형",
      "hook": "이 타입으로 상세페이지를 만들 때의 핵심 한 줄 전략 (40자 이내)",
      "appeal_analysis": {{
        "target_customer": "구체적 타겟 묘사 (예: 첫 이유식 앞에 막막한 7개월 아기 엄마)",
        "core_pain": "고객의 핵심 고통을 고객 언어로 (예: 뭘 먹여야 하는지 모르겠고, 직접 만들기엔 너무 힘들다)",
        "buy_trigger": "구매 결심 순간의 심리 (예: 내 아이 건강은 타협하고 싶지 않다)",
        "appeal_points": ["소구포인트1", "소구포인트2", "소구포인트3"]
      }},
      "sections": [
        {{"no": 1, "name": "고객 감정/상황 중심 섹션명 (10자 이내)", "purpose": "이 섹션에서 고객에게 심어야 할 감정/생각 (30자 이내)"}},
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
      "appeal_analysis": {{"target_customer": "...", "core_pain": "...", "buy_trigger": "...", "appeal_points": ["...", "...", "..."]}},
      "sections": [{{"no": 1, "name": "...", "purpose": "..."}}, {{"no": 2, "name": "...", "purpose": "..."}}, {{"no": 3, "name": "...", "purpose": "..."}}, {{"no": 4, "name": "...", "purpose": "..."}}, {{"no": 5, "name": "...", "purpose": "..."}}, {{"no": 6, "name": "...", "purpose": "..."}}]
    }},
    {{
      "type_name": "데이터·전문가형",
      "hook": "...",
      "appeal_analysis": {{"target_customer": "...", "core_pain": "...", "buy_trigger": "...", "appeal_points": ["...", "...", "..."]}},
      "sections": [{{"no": 1, "name": "...", "purpose": "..."}}, {{"no": 2, "name": "...", "purpose": "..."}}, {{"no": 3, "name": "...", "purpose": "..."}}, {{"no": 4, "name": "...", "purpose": "..."}}, {{"no": 5, "name": "...", "purpose": "..."}}, {{"no": 6, "name": "...", "purpose": "..."}}]
    }}
  ]
}}"""

    return system, user


# ── Phase 2: 선택된 1타입 카피 생성 ─────────────────────────
def build_copy_prompt(brand: dict, input_data: dict, plan_preview: dict) -> tuple[str, str]:
    """선택된 플랜의 섹션별 카피 + 영문 이미지 프롬프트 동시 생성."""
    brand_ctx = build_brand_context(brand)
    type_name = plan_preview.get('type_name', '')
    hook      = plan_preview.get('hook', '')
    sections  = plan_preview.get('sections', [])
    appeal    = plan_preview.get('appeal_analysis', {})

    product_name = input_data.get('product_name', '')
    features     = input_data.get('features', '')
    target       = input_data.get('target_customer') or appeal.get('target_customer', '')
    diff         = input_data.get('differentiator') or ''
    price        = input_data.get('price_range') or ''
    pain         = appeal.get('core_pain', '')
    trigger      = appeal.get('buy_trigger', '')
    ap_points    = appeal.get('appeal_points', [])

    strategy = _TYPE_STRATEGIES.get(type_name, '')

    # 섹션별 카피 가이드 생성
    sec_guides = []
    for s in sections:
        sec_guides.append(
            f"섹션 {s['no']} [{s['name']}]\n"
            f"  목적: {s.get('purpose','')}\n"
            f"  카피 방향: 이 섹션에서 고객이 '{s.get('purpose','')}' 느끼도록 작성"
        )
    sec_block = '\n\n'.join(sec_guides)

    system = f"""당신은 대한민국 최고 수준의 온라인 커머스 카피라이터입니다.
전환율 최적화(CRO) 전문가로서, 고객 심리 기반의 상세페이지 카피를 작성합니다.

핵심 원칙:
1. 고객 중심 언어: "우리 제품은 ~합니다" 대신 "당신의 ~고민을 해결합니다"
2. 구체성: 막연한 형용사 금지 — 숫자, 상황, 감각적 묘사로 대체
3. 감정 먼저: 기능 설명 전에 공감/감정을 먼저 건드림
4. 자연스러운 흐름: 각 섹션이 다음 섹션으로 자연스럽게 이어져야 함
5. 진짜 고객 언어: 브로셔 언어가 아닌 실제 후기/대화에서 나올 법한 표현

결과는 순수 JSON만 출력합니다. 마크다운 없음.

브랜드: {brand_ctx}"""

    user = f"""[상품 정보]
상품명: {product_name}
핵심 특징: {features}
타겟 고객: {target}
가격대: {price or '-'}
경쟁 차별점: {diff or '-'}

[고객 심리 분석]
핵심 고통 (고객의 언어로): {pain}
구매 결심 트리거: {trigger}
이 타입에서 강조할 소구 포인트: {', '.join(ap_points)}

[선택된 기획 타입]
타입: {type_name}
핵심 전략: {hook}
{strategy}

[섹션별 카피 작성]
{sec_block}

[카피 작성 규칙]
■ 구조: 헤드라인(첫 줄) + 본문(2~3줄)
■ 헤드라인: 12자 이내
  - 공감형: 고객 상황을 꼭 집어 말하는 문장 (예: "매번 후회했습니다")
  - 스토리형: 장면/감정을 담은 한 줄 (예: "그날 아침이 달라졌습니다")
  - 데이터형: 숫자/사실로 시작 (예: "3,847명이 선택한 이유")
■ 본문: 각 줄 28자 이내, 2~3줄
  - 첫 줄: 고객 상황/감정에 공감 또는 문제 구체화
  - 둘째 줄: 이 제품이 어떻게 다른지 / 해결책
  - 셋째 줄(선택): 구체적 근거 또는 행동 유도
■ 금지 표현: "최고", "최상", "최저", "품질 좋은", "믿을 수 있는", "정성껏", "특별한"
■ 이모지·특수기호·별표 없이 순수 텍스트만
■ 전체 6개 섹션이 하나의 설득 이야기로 읽혀야 함

[image_prompt 규칙 — FLUX AI 전송용]
- 반드시 영어로만 작성 (한글 절대 금지)
- {product_name} 실제 제품이 주인공인 장면
- 섹션의 감정/목적과 일치하는 촬영 스타일 (공감 섹션 → 따뜻한 생활 장면 / 데이터 섹션 → 클린 스튜디오)
- 20단어 이내, 구체적 촬영 설정
- no text, no words, no letters in the image

Output ONLY this JSON:
{{
  "copies": [
    {{"no": 1, "copy": "헤드라인\\n본문 첫줄\\n본문 둘째줄", "image_prompt": "English scene description"}},
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
