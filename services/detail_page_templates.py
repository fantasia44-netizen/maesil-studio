"""상세페이지 빌더 — 10가지 고객 서사 시나리오 템플릿 정의"""

TEMPLATES = [
    {
        'id': 'empathy',
        'name': '공감형',
        'desc': '이런 불편함 있으셨죠? → 공감 → 해결책',
        'icon': 'bi-heart-pulse',
        'color': '#e74c3c',
        'narrative': '고객의 불편함에 먼저 공감하고, 그 해결책으로 자연스럽게 유도합니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',     'label': '공감 헤드라인',   'placeholder': '예: 혹시 이런 불편함 겪고 계신가요?'},
            {'type': 'image', 'role': 'problem',  'label': '문제 상황 이미지'},
            {'type': 'text',  'role': 'empathy',  'label': '공감 본문',       'placeholder': '고객의 불편함을 구체적으로 묘사합니다. 그들의 언어로 이야기하세요.'},
            {'type': 'image', 'role': 'solution', 'label': '해결책 이미지'},
            {'type': 'text',  'role': 'product',  'label': '제품 소개',       'placeholder': '이 문제를 어떻게 해결하는지 설명합니다'},
            {'type': 'text',  'role': 'cta',      'label': 'CTA 문구',        'placeholder': '예: 지금 바로 경험해 보세요'},
        ],
    },
    {
        'id': 'problem_solution',
        'name': '문제-해결형',
        'desc': '문제 제기 → 원인 분석 → 제품이 답 → CTA',
        'icon': 'bi-lightbulb',
        'color': '#f39c12',
        'narrative': '문제를 명확히 짚고 원인을 분석한 뒤, 제품이 최적의 해결책임을 보여줍니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',     'label': '문제 제기 헤드라인', 'placeholder': '예: 왜 계속 같은 실수를 반복할까요?'},
            {'type': 'text',  'role': 'cause',    'label': '원인 분석',         'placeholder': '문제의 근본 원인을 논리적으로 설명합니다'},
            {'type': 'image', 'role': 'solution', 'label': '해결책 이미지'},
            {'type': 'text',  'role': 'product',  'label': '제품 특징',         'placeholder': '이 문제를 해결하는 제품의 핵심 기능'},
            {'type': 'text',  'role': 'review',   'label': '고객 후기',         'placeholder': '⭐⭐⭐⭐⭐ 실제 고객 후기'},
            {'type': 'text',  'role': 'cta',      'label': 'CTA 문구',          'placeholder': '예: 지금 해결하세요'},
        ],
    },
    {
        'id': 'before_after',
        'name': 'Before/After형',
        'desc': '사용 전 상황 → 사용 후 변화 → 결과 보장',
        'icon': 'bi-arrow-left-right',
        'color': '#27ae60',
        'narrative': '극적인 변화를 시각적으로 보여주어 제품의 효과를 직접적으로 증명합니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',    'label': '변화 헤드라인',  'placeholder': '예: 단 2주 만에 이런 변화가 생겼습니다'},
            {'type': 'image', 'role': 'before',  'label': 'Before 이미지'},
            {'type': 'text',  'role': 'before',  'label': 'Before 설명',   'placeholder': '사용 전 상황을 구체적으로 설명합니다'},
            {'type': 'image', 'role': 'after',   'label': 'After 이미지'},
            {'type': 'text',  'role': 'after',   'label': 'After 설명',    'placeholder': '사용 후 달라진 점을 설명합니다'},
            {'type': 'text',  'role': 'review',  'label': '실제 후기',     'placeholder': '⭐⭐⭐⭐⭐ 실제 사용자의 변화 후기'},
            {'type': 'text',  'role': 'cta',     'label': 'CTA 문구',      'placeholder': '예: 나도 변화를 경험하고 싶어요'},
        ],
    },
    {
        'id': 'comparison',
        'name': '비교형',
        'desc': '기존 방법의 한계 → 우리 제품의 차이 → 선택 이유',
        'icon': 'bi-bar-chart-steps',
        'color': '#8e44ad',
        'narrative': '기존 방법과 비교하여 제품의 우월성을 명확하게 보여줍니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',       'label': '비교 헤드라인',      'placeholder': '예: 기존 제품과 무엇이 다를까요?'},
            {'type': 'text',  'role': 'cause',      'label': '기존 방법의 한계',    'placeholder': '기존 제품/방법의 문제점을 나열합니다'},
            {'type': 'image', 'role': 'comparison', 'label': '비교 이미지/표'},
            {'type': 'text',  'role': 'product',    'label': '우리 제품의 차이점', 'placeholder': '차별화된 특징과 장점을 설명합니다'},
            {'type': 'text',  'role': 'cta',        'label': 'CTA 문구',           'placeholder': '예: 직접 비교해 보세요'},
        ],
    },
    {
        'id': 'story',
        'name': '스토리형',
        'desc': '개발 배경 → 고민 과정 → 탄생 스토리',
        'icon': 'bi-book',
        'color': '#2980b9',
        'narrative': '브랜드의 진정성 있는 스토리로 고객과 감정적 유대를 형성합니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',    'label': '스토리 오프닝', 'placeholder': '예: 이 제품은 제 딸아이의 피부 때문에 시작됐습니다'},
            {'type': 'image', 'role': 'story',   'label': '스토리 이미지'},
            {'type': 'text',  'role': 'story',   'label': '개발 배경',     'placeholder': '제품이 탄생하게 된 진짜 이야기를 씁니다'},
            {'type': 'text',  'role': 'product', 'label': '제품 철학',     'placeholder': '이 제품에 담긴 가치와 철학을 설명합니다'},
            {'type': 'image', 'role': 'product', 'label': '제품 이미지'},
            {'type': 'text',  'role': 'cta',     'label': 'CTA 문구',      'placeholder': '예: 우리의 스토리에 함께해 주세요'},
        ],
    },
    {
        'id': 'data_proof',
        'name': '데이터·증거형',
        'desc': '수치로 증명 → 연구·인증 → 신뢰 구축',
        'icon': 'bi-graph-up-arrow',
        'color': '#16a085',
        'narrative': '객관적인 데이터와 증거로 제품의 효과를 과학적으로 입증합니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',    'label': '데이터 헤드라인',  'placeholder': '예: 98%의 고객이 만족한 이유'},
            {'type': 'text',  'role': 'data',    'label': '핵심 수치/통계',   'placeholder': '예: 재구매율 78% | 평점 4.9 | 누적 판매 3만 개'},
            {'type': 'image', 'role': 'data',    'label': '데이터/인증서 이미지'},
            {'type': 'text',  'role': 'product', 'label': '성분·기술 설명',   'placeholder': '연구 결과와 근거 기반 설명'},
            {'type': 'text',  'role': 'review',  'label': '고객 후기',        'placeholder': '⭐⭐⭐⭐⭐ 데이터를 뒷받침하는 실제 후기'},
            {'type': 'text',  'role': 'cta',     'label': 'CTA 문구',         'placeholder': '예: 숫자로 검증된 제품을 경험하세요'},
        ],
    },
    {
        'id': 'lifestyle',
        'name': '라이프스타일형',
        'desc': '이상적 일상 → 제품이 만드는 변화 → 감성 연결',
        'icon': 'bi-sun',
        'color': '#e67e22',
        'narrative': '고객이 꿈꾸는 이상적인 라이프스타일과 제품을 자연스럽게 연결합니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',      'label': '라이프스타일 헤드라인', 'placeholder': '예: 바쁜 아침, 여유롭게 시작하는 방법'},
            {'type': 'image', 'role': 'lifestyle', 'label': '라이프스타일 이미지'},
            {'type': 'text',  'role': 'lifestyle', 'label': '이상적 일상 묘사',      'placeholder': '고객이 원하는 일상의 모습을 그립니다'},
            {'type': 'image', 'role': 'product',   'label': '제품 사용 장면'},
            {'type': 'text',  'role': 'product',   'label': '제품 연결',             'placeholder': '이 라이프스타일을 만들어주는 제품 설명'},
            {'type': 'text',  'role': 'cta',       'label': 'CTA 문구',              'placeholder': '예: 나만의 루틴을 시작해 보세요'},
        ],
    },
    {
        'id': 'expert',
        'name': '전문가 추천형',
        'desc': '전문가 인증 → 성분·기술 → 신뢰 기반 구매',
        'icon': 'bi-shield-check',
        'color': '#34495e',
        'narrative': '전문가의 권위와 인증을 통해 제품에 대한 신뢰를 극대화합니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',    'label': '신뢰 헤드라인',     'placeholder': '예: 피부과 전문의가 직접 개발했습니다'},
            {'type': 'image', 'role': 'expert',  'label': '전문가/인증 이미지'},
            {'type': 'text',  'role': 'expert',  'label': '전문가 코멘트',     'placeholder': '"전문가의 말을 직접 인용합니다"'},
            {'type': 'text',  'role': 'product', 'label': '성분·기술 설명',    'placeholder': '전문적인 성분과 기술을 쉽게 설명합니다'},
            {'type': 'image', 'role': 'product', 'label': '제품 이미지'},
            {'type': 'text',  'role': 'cta',     'label': 'CTA 문구',          'placeholder': '예: 전문가가 선택한 제품을 만나보세요'},
        ],
    },
    {
        'id': 'fomo',
        'name': 'FOMO형',
        'desc': '이미 쓰는 사람들 → 놓치고 있는 것 → 지금 시작',
        'icon': 'bi-people',
        'color': '#c0392b',
        'narrative': '많은 사람들이 이미 경험하고 있다는 사실로 구매 욕구를 자극합니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',      'label': 'FOMO 헤드라인',  'placeholder': '예: 이미 3만 명이 경험하고 있습니다'},
            {'type': 'image', 'role': 'lifestyle', 'label': '커뮤니티/사용자 이미지'},
            {'type': 'text',  'role': 'fomo',      'label': '사회적 증거',    'placeholder': '얼마나 많은 사람들이 쓰고 있는지 보여줍니다'},
            {'type': 'text',  'role': 'product',   'label': '제품 소개',      'placeholder': '왜 이렇게 많은 사람들이 선택했는지'},
            {'type': 'text',  'role': 'review',    'label': '실제 후기',      'placeholder': '⭐⭐⭐⭐⭐ 여러 사람의 후기'},
            {'type': 'text',  'role': 'cta',       'label': 'CTA 문구',       'placeholder': '예: 지금 합류하세요'},
        ],
    },
    {
        'id': 'value',
        'name': '가성비·혜택형',
        'desc': '비용 대비 효과 → 경쟁 비교 → 한정 혜택',
        'icon': 'bi-tags',
        'color': '#1abc9c',
        'narrative': '합리적인 가격과 풍부한 혜택으로 구매 결정을 쉽게 만들어줍니다.',
        'blocks': [
            {'type': 'text',  'role': 'hook',       'label': '혜택 헤드라인',  'placeholder': '예: 가격은 절반, 효과는 두 배'},
            {'type': 'text',  'role': 'benefit',    'label': '혜택 목록',      'placeholder': '✓ 혜택 1\n✓ 혜택 2\n✓ 혜택 3'},
            {'type': 'image', 'role': 'comparison', 'label': '가격 비교 이미지'},
            {'type': 'text',  'role': 'product',    'label': '제품 구성',      'placeholder': '구성품과 가격을 상세히 설명합니다'},
            {'type': 'text',  'role': 'fomo',       'label': '한정 혜택',      'placeholder': '예: 이번 달까지만 특별 구성 제공'},
            {'type': 'text',  'role': 'cta',        'label': 'CTA 문구',       'placeholder': '예: 지금 바로 구매하기'},
        ],
    },
]

TEMPLATE_MAP = {t['id']: t for t in TEMPLATES}


def list_templates() -> list:
    return TEMPLATES


def get_template(template_id: str) -> dict | None:
    return TEMPLATE_MAP.get(template_id)
