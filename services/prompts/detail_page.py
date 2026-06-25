"""상세페이지 소구포인트 기획서 + 초안 제안서 프롬프트"""
from services.claude_service import build_brand_context


def build_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    """레거시 호환용 — 현재는 build_preview_prompt + build_copy_prompt 사용."""
    return build_preview_prompt(brand, input_data)


# ── Phase 0: 상품 진단 ───────────────────────────────────────
def build_diagnosis_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    """상품 진단 — 3가지 타입 추천 점수 + 구매이유/망설임 추출."""
    brand_ctx    = build_brand_context(brand)
    product_name = input_data.get('product_name', '')
    features     = input_data.get('features', '')
    target       = input_data.get('target_customer') or ''
    diff         = input_data.get('differentiator') or ''
    price        = input_data.get('price_range') or ''
    certs        = input_data.get('certifications') or ''
    questions    = input_data.get('customer_questions') or ''
    reviews      = input_data.get('customer_reviews') or ''

    extra_lines = []
    if certs:      extra_lines.append(f'인증·수치: {certs}')
    if questions:  extra_lines.append(f'고객 자주 묻는 질문:\n{questions}')
    if reviews:    extra_lines.append(f'실제 후기:\n{reviews}')
    extra_str = ('\n' + '\n\n'.join(extra_lines)) if extra_lines else ''

    system = """당신은 온라인 커머스 전환율 전략가입니다.
상품 정보를 보고 어떤 상세페이지 전략이 가장 구매전환에 효과적인지 진단합니다.
결과는 순수 JSON만 출력합니다. 마크다운 없음."""

    user = f"""아래 상품을 진단하고 최적 상세페이지 전략을 추천해 주세요.

[브랜드]
{brand_ctx}

[상품 정보]
상품명: {product_name}
핵심 특징: {features}
타겟 고객: {target or '-'}
가격대: {price or '-'}
차별점: {diff or '-'}{extra_str}

[진단 대상 타입]
- 공감·문제해결형 (PAS): 고객 불편 공감 → 해결책 제시
- 스토리·라이프스타일형 (BAB): 원하는 삶 먼저 → 제품은 다리
- 데이터·전문가형 (ACCA): 수치·인증·비교로 이성 납득

[판단 기준]
- 후기·입소문이 강한 상품 → 공감형 유리
- 감성/라이프스타일 브랜드 → 스토리형 유리
- 기능 차이가 명확하거나 건강/전문성 상품 → 데이터형 유리

Output ONLY this JSON:
{{
  "product_type": "이 상품의 성격 한 줄 (예: 육아맘 대상 간편 이유식)",
  "key_purchase_reason": "이 상품을 사는 가장 큰 이유 (고객 언어로)",
  "key_hesitation": "가장 큰 구매 망설임 (고객 언어로)",
  "recommendations": [
    {{"type_name": "공감·문제해결형", "score": 5, "reason": "이 타입을 추천/비추천하는 이유 1~2줄"}},
    {{"type_name": "스토리·라이프스타일형", "score": 3, "reason": "..."}},
    {{"type_name": "데이터·전문가형", "score": 2, "reason": "..."}}
  ],
  "top_appeal_points": ["이 상품의 핵심 소구포인트1", "포인트2", "포인트3"]
}}"""

    return system, user


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
    '공감·문제해결형': {
        'framework': 'PAS (Problem → Agitation → Solution)',
        'core_logic': '고객이 느끼는 불편을 먼저 말해줌으로써 "이 브랜드는 나를 안다"는 신뢰 형성. 제품 설명 전에 공감부터.',
        'customer_journey': [
            ('공감 훅',    '아직 이 문제 모르세요?',      '"혹시 나만 이런 건가?" — 고객이 자신의 상황이 묘사되는 걸 보며 스크롤을 멈춤'),
            ('문제 심화',  '알면서도 못 해결한 이유',      '"맞아, 그래서 계속 힘들었구나" — 기존 해결책이 왜 실패했는지 대신 말해줌'),
            ('해결책 등장','드디어 다른 방식',             '"어떻게 다른 거지?" — 기대감과 호기심 유발'),
            ('납득 근거',  '왜 이게 효과 있나',           '"믿어도 될까?" — 작동 원리/성분/기술로 이성적 납득'),
            ('사회적 증거','나만 고민한 게 아니었네',      '"나랑 똑같은 사람들이 해결했구나" — 유사 고객의 변화 스토리'),
            ('결심 유도',  '지금 결정해도 되는 이유',      '"혹시 후회하면 어떡하지?" 반론 제거 + 지금 선택의 이점'),
        ],
    },
    '스토리·라이프스타일형': {
        'framework': 'BAB (Before → After → Bridge)',
        'core_logic': '제품이 아니라 고객이 원하는 삶의 모습을 먼저 팔고, 제품은 그 삶으로 가는 다리로 등장.',
        'customer_journey': [
            ('비전 제시',  '이런 삶 원하시죠?',           '"맞아, 나도 이렇게 살고 싶어" — 꿈꾸는 일상 장면을 먼저 보여줌'),
            ('현실 대비',  '근데 지금은 이렇죠',           '"맞아, 현실은 이렇지…" — 갭을 직시하게 해서 변화 욕구 자극'),
            ('브랜드 철학','우리가 이 갭을 메우는 방식',   '"이 브랜드가 나의 가치관과 맞네" — 공감 → 팬심 형성'),
            ('경험/제품',  '이걸 쓰면 달라지는 순간',      '"실제로 어떻게 달라지는 거지?" — 사용 장면을 감각적으로 묘사'),
            ('변화 스토리','먼저 경험한 사람들',           '"나도 저렇게 될 수 있겠다" — 구체적 Before/After'),
            ('초대',       '당신의 이야기를 시작하세요',   '"나도 해볼까?" — 브랜드 참여/커뮤니티 소속감'),
        ],
    },
    '데이터·전문가형': {
        'framework': 'ACCA (Awareness → Comprehension → Conviction → Action)',
        'core_logic': '수치·인증·비교로 이성적 납득 → 신뢰. "전문가도 인정한, 데이터로 증명된" 포지셔닝.',
        'customer_journey': [
            ('임팩트 수치','이 숫자 보셨나요?',            '"오, 이게 사실이야?" — 놀라운 수치로 스크롤 멈춤'),
            ('문제 정의',  '시장의 불편한 진실',           '"이런 문제가 있는 줄 몰랐네" — 카테고리 문제를 데이터로 제시'),
            ('기술 차별화','어떻게 다른가, 정확히',        '"경쟁 제품이랑 뭐가 다르지?" — 스펙 비교로 명확한 차별화'),
            ('전문가 검증','전문가/기관이 확인한 것들',     '"누군가 검증했나?" — 인증/수상/미디어로 권위 형성'),
            ('실증 데이터','사용 결과, 숫자로 말함',       '"실제로 효과 있나?" — 리뷰 통계/비교 실험/사용 전후'),
            ('선택 정리',  '지금 선택이 합리적인 이유',    '"가성비 있나? 후회 안 하나?" — 구성·가격·보증으로 마무리'),
        ],
    },
}


def build_preview_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    """3타입 미리보기 — 섹션 구조 + 소구 전략 분석. 카피 없음."""
    brand_ctx    = build_brand_context(brand)
    product_name = input_data.get('product_name', '')
    features     = input_data.get('features', '')
    target       = input_data.get('target_customer') or ''
    diff         = input_data.get('differentiator') or ''
    price        = input_data.get('price_range') or ''
    certs        = input_data.get('certifications') or ''
    questions    = input_data.get('customer_questions') or ''
    reviews      = input_data.get('customer_reviews') or ''

    # 타입별 여정 블록 생성
    type_blocks = []
    for t_name, t_info in _TYPE_STRATEGIES.items():
        journey_lines = '\n'.join(
            f"  {i+1}단계 [{role}] 고객 심리: {psych}"
            for i, (role, _, psych) in enumerate(t_info['customer_journey'])
        )
        type_blocks.append(
            f'타입: "{t_name}"\n'
            f'프레임워크: {t_info["framework"]}\n'
            f'핵심 논리: {t_info["core_logic"]}\n'
            f'구매 여정:\n{journey_lines}'
        )
    type_block_str = '\n\n'.join(type_blocks)

    system = f"""{_PREVIEW_SYSTEM}

[브랜드 정보]
{brand_ctx}"""

    renewal_block = ''
    if renewal_url:
        renewal_block = f"""
[리뉴얼 분석 요청]
기존 상세페이지 URL: {renewal_url}
→ 이 URL의 상세페이지가 가진 일반적 약점(스크롤 이탈 구간, 신뢰 부족, 공감 부재 등)을 추론하고,
  각 타입의 섹션 구조에 그 개선 방향을 반영해 주세요.
  appeal_analysis.core_pain과 buy_trigger에 기존 상세페이지의 미충족 요소를 명시해 주세요.
"""

    extra_blocks = []
    if certs:
        extra_blocks.append(f'인증·수치: {certs}')
    if questions:
        extra_blocks.append(f'고객 자주 묻는 질문 (구매 망설임 포인트):\n{questions}')
    if reviews:
        extra_blocks.append(f'실제 고객 후기 (이 언어를 appeal_analysis 작성에 적극 반영):\n{reviews}')
    extra_str = ('\n\n[고객 목소리 데이터]\n' + '\n\n'.join(extra_blocks)) if extra_blocks else ''

    user = f"""아래 상품에 대해 3가지 상세페이지 타입의 섹션 구조를 설계해 주세요.{renewal_block}

[상품 정보]
상품명: {product_name}
핵심 특징: {features}
타겟 고객: {target or '브랜드 프로필 기준'}
가격대: {price or '-'}
경쟁 차별점: {diff or '-'}{extra_str}

[3가지 타입과 구매 여정]
{type_block_str}

[설계 원칙 — 반드시 준수]
1. 섹션명은 마케터 시선이 아닌 고객 시선으로
   - 금지: "제품 소개", "특징 설명", "구성품 안내", "브랜드 스토리"
   - 허용: 고객이 그 순간 느끼는 감정/의문/상황을 담은 표현
   - 예시(이유식 제품): "매일 밤 고민하셨죠?", "이걸 먹여도 될까요?", "엄마들이 먼저 알아봤습니다"

2. purpose는 "이 섹션에서 고객 머릿속에 심어야 할 생각" — 마케터의 의도가 아님
   - 금지: "제품 특징을 설명한다", "브랜드를 소개한다"
   - 허용: "'나도 이 문제 있는데' 공감 유발", "'이게 정말 다른 건가?' 호기심 자극"

3. appeal_analysis — 이 상품·이 타입으로 구매를 이끌어내는 전략 핵심
   - target_customer: 실제 이 상품을 살 사람의 구체적 상황 묘사 (직업·나이 말고 상황으로)
   - core_pain: 고객이 매일 겪는 가장 구체적인 불편 — 고객이 직접 할 말투로
   - buy_trigger: 구매 버튼을 누르게 만드는 심리적 마지막 한 마디
   - appeal_points: 이 타입 전략에서 가장 강하게 쓸 소구 포인트 3가지

Output ONLY this JSON (no markdown, no explanation):
{{
  "plans": [
    {{
      "type_name": "공감·문제해결형",
      "hook": "이 타입으로 상세페이지를 만들 때의 핵심 전략 한 줄 (40자 이내)",
      "appeal_analysis": {{
        "target_customer": "구체적 상황 묘사 — 예: '첫 이유식 앞에서 뭘 먹여야 할지 몰라 검색만 반복하는 7개월 아기 엄마'",
        "core_pain": "고객 목소리로 — 예: '직접 만들자니 너무 힘들고, 시판은 뭘 넣었는지 불안하고'",
        "buy_trigger": "예: '내 아이 것만큼은 타협하기 싫다'",
        "appeal_points": ["포인트1", "포인트2", "포인트3"]
      }},
      "sections": [
        {{"no": 1, "name": "고객 감정/의문 중심 섹션명 (12자 이내)", "purpose": "이 섹션에서 고객 머릿속에 심어야 할 생각 (35자 이내)"}},
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
    certs        = input_data.get('certifications') or ''
    questions    = input_data.get('customer_questions') or ''
    reviews      = input_data.get('customer_reviews') or ''
    pain         = appeal.get('core_pain', '')
    trigger      = appeal.get('buy_trigger', '')
    ap_points    = appeal.get('appeal_points', [])

    # 타입별 구매 여정에서 섹션별 고객 심리 매핑
    journey = _TYPE_STRATEGIES.get(type_name, {}).get('customer_journey', [])
    framework = _TYPE_STRATEGIES.get(type_name, {}).get('framework', '')
    core_logic = _TYPE_STRATEGIES.get(type_name, {}).get('core_logic', '')

    # 섹션 × 구매 여정 단계 매칭 (순서 기반)
    sec_guides = []
    for i, sec in enumerate(sections):
        j_role, j_angle, j_psych = journey[i] if i < len(journey) else ('', '', '')
        sec_guides.append(
            f"섹션 {sec['no']} [{sec['name']}]\n"
            f"  이 섹션의 역할: {j_role}\n"
            f"  고객의 내면 목소리: {j_psych}\n"
            f"  섹션 목적: {sec.get('purpose', '')}\n"
            f"  카피 각도: {j_angle}\n"
            f"  → 카피는 고객의 내면 목소리에 먼저 공감/응답한 뒤 제품으로 연결할 것"
        )
    sec_block = '\n\n'.join(sec_guides)

    system = f"""당신은 대한민국 최고 수준의 온라인 커머스 카피라이터입니다.
전환율 최적화(CRO) 전문가로서 고객 구매 심리 기반의 상세페이지 카피를 작성합니다.

[카피라이팅 핵심 철학]
상세페이지 카피의 목적은 제품을 설명하는 게 아니라,
고객이 "맞아, 나 얘기네" → "이게 해결책이구나" → "사야겠다"로
자연스럽게 이동하도록 이끄는 것입니다.

규칙:
- 제품 기능을 말하기 전에 반드시 고객 감정/상황부터 건드릴 것
- 모든 기능은 고객 편익으로 변환: "A 기능이 있습니다" → "A 덕분에 당신은 B를 얻습니다"
- 고객이 후기에 쓸 법한 진짜 언어 사용 (브로셔 문체 금지)
- 6개 섹션은 하나의 단편 드라마다: 각 섹션이 이전 섹션의 감정을 이어받아 자연스럽게 흘러야 함
  섹션1에서 생긴 공감이 섹션2에서 심화되고, 섹션3에서 전환점이 오고, 섹션4-5에서 납득되고, 섹션6에서 결심으로 닫혀야 함

결과는 순수 JSON만 출력합니다. 마크다운 없음.
브랜드 컨텍스트: {brand_ctx}"""

    renewal_copy_block = ''
    if renewal_url:
        renewal_copy_block = f"""
[리뉴얼 컨텍스트]
기존 상세페이지: {renewal_url}
→ 기존 페이지의 약점(공감 부족, 신뢰 미흡, 차별화 불명확 등)을 보완하는 방향으로 카피를 강화해 주세요.
  특히 헤드라인과 첫 2개 섹션에서 기존 페이지가 놓쳤을 고객 공감 포인트를 살려주세요.

"""

    user = f"""{renewal_copy_block}[상품 정보]
상품명: {product_name}
핵심 특징·기능: {features}
타겟 고객: {target}
가격대: {price or '-'}
경쟁 차별점: {diff or '-'}
인증·수치: {certs or '-'}

[고객 심리 분석 — 카피 작성의 핵심 나침반]
핵심 고통 (고객의 언어 그대로): {pain}
구매 결심 트리거: {trigger}
강조할 소구 포인트: {', '.join(ap_points)}
{f"고객 자주 묻는 질문 (카피에서 선제 해소할 것):{chr(10)}{questions}" if questions else ''}
{f"실제 고객 후기 (이 언어톤과 감정을 카피에 직접 살릴 것):{chr(10)}{reviews}" if reviews else ''}

[선택 타입: {type_name}]
프레임워크: {framework}
핵심 논리: {core_logic}
전략 방향: {hook}

[섹션별 작성 가이드]
{sec_block}

[카피 작성 규칙]
■ 구조: 헤드라인(1줄) + 본문(2~3줄)

■ 헤드라인 (18자 이내) — 타입별 패턴:
  공감형 → 고객 상황/고통을 직접 꼬집는 문장. 감정이 충분히 실려야 함
    예: "매일 밤 지쳐서 쓰러지기 직전이었어요" / "아이가 거부할까봐 겁이 났어요"
  스토리형 → 장면·감정이 담긴 감각적 한 줄
    예: "그 아침이 달라졌습니다" / "처음엔 반신반의했는데 지금은"
  데이터형 → 숫자·사실로 시작하는 임팩트 문장
    예: "4,200명 중 98%가 재구매한 이유" / "일반 제품과 성분 직접 비교해봤습니다"

■ 본문 (각 줄 30자 이내, 2~3줄):
  줄1: 고객 상황 공감 또는 문제 구체화 — "~하셨죠?" / "~때문에 ~하셨을 거예요"
  줄2: 이 제품이 어떻게 그걸 해결하는지 — 기능이 아닌 고객이 얻는 결과
  줄3(선택): 구체적 근거(수치/성분/후기) 또는 다음 섹션으로 자연스럽게 이어지는 여운

■ 섹션6 (마지막) 전용 규칙 — 결심 유도:
  헤드라인: 지금 결정해도 되는 이유를 한 줄로 — 반론 제거 또는 기회비용 자극
    예: "한 번 써보면 다시 직접 만들기 싫어집니다" / "후회는 안 사고 나서 했을 때만"
  본문: 구체적 행동(첫 주문, 시작하기 등) + 안심 요소(교환/환불/인증) + 브랜드명 1회 자연스럽게 노출
  절대 금지: "오늘 선택해도 괜찮아요" 같은 힘없는 마무리

■ 절대 금지 표현:
  "최고", "최상", "최저가", "품질 좋은", "믿을 수 있는", "정성껏 만든",
  "특별한", "엄선된", "프리미엄", "합리적인", "괜찮아요" — 이런 단어는 아무 의미가 없음

■ 기능→편익 변환 필수 (예시):
  X: "HACCP 인증을 받았습니다"
  O: "아이 입에 들어가는 거, 인증 없는 건 이제 못 사겠더라고요 — HACCP 통과"
  X: "냉동 큐브 30개 구성"
  O: "30분에 한 달치 이유식 완성. 그 시간에 아이 옆에 있어주세요"

■ 이모지·특수기호·별표 없이 순수 텍스트만

[image_prompt 규칙 — FLUX AI 전송용 영어만]
- 반드시 {product_name} 실제 제품이 화면에 등장해야 함 (사람 단독 컷 금지)
- 섹션별 제품 등장 방식:
  섹션1~2 (공감/문제): product subtly placed in background, person in foreground showing emotion
  섹션3~4 (해결/증거): product as hero, hands interacting with product, clean environment
  섹션5 (후기/변화): product in use, lifestyle context, authentic natural setting
  섹션6 (결심): product packaging beauty shot, gift-ready or ready-to-use styling, warm light
- Scene과 Composition을 분리해서 작성:
  scene: [장면 묘사 — 어디서, 누가, 무엇을]
  composition: [촬영 스타일 — 빛, 각도, 렌즈, 분위기]
- no text no words no letters in the image

Output ONLY this JSON:
{{
  "copies": [
    {{"no": 1, "copy": "헤드라인\\n본문줄1\\n본문줄2", "image_prompt": "scene: [장면]. composition: [스타일]"}},
    {{"no": 2, "copy": "...", "image_prompt": "scene: [...]. composition: [...]"}},
    {{"no": 3, "copy": "...", "image_prompt": "scene: [...]. composition: [...]"}},
    {{"no": 4, "copy": "...", "image_prompt": "scene: [...]. composition: [...]"}},
    {{"no": 5, "copy": "...", "image_prompt": "scene: [...]. composition: [...]"}},
    {{"no": 6, "copy": "...", "image_prompt": "scene: [...]. composition: [...]"}}
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
