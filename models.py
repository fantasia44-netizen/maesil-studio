"""매실 스튜디오 - 사용자 모델 + 요금제 + 포인트 비용"""
from flask_login import UserMixin
from functools import wraps
from flask import flash, redirect, url_for
from flask_login import current_user


# ──────────────────────────────────────────
# 요금제별 기능 플래그
# ──────────────────────────────────────────
PLAN_FEATURES = {
    'free': {
        'label': '무료',
        'price': 0,
        'monthly_points': 0,
        'brand_profiles': 1,
        'image_gen': False,
        'brand_kit': False,
    },
    'starter': {
        'label': 'Starter',
        'price': 9900,
        'monthly_points': 3000,
        'brand_profiles': 1,
        'image_gen': False,
        'brand_kit': False,
    },
    'growth': {
        'label': 'Growth',
        'price': 24900,
        'monthly_points': 10000,
        'brand_profiles': 3,
        'image_gen': True,
        'brand_kit': True,
    },
    'pro': {
        'label': 'Pro',
        'price': 49900,
        'monthly_points': 25000,
        'brand_profiles': 5,
        'image_gen': True,
        'brand_kit': True,
    },
}

# ──────────────────────────────────────────
# 포인트 비용표
# ──────────────────────────────────────────
POINT_COSTS = {
    # 텍스트 생성 (Claude)
    'blog':            40,   # 1,000자 기준 (분량별 차등 — get_blog_cost 참조)
    'instagram':       30,
    'detail_page':    150,
    'thumbnail_text':  40,
    'ad_copy':         60,
    'press_release':  200,
    'brand_package': 1500,
    'product_launch': 2000,
    # 이미지 생성 — 엔진별
    'img_preview':     50,   # FLUX Schnell — 빠른 라이프스타일 씬
    'img_standard':   300,   # FLUX Pro — 브랜드 에셋
    'img_hq':         600,   # FLUX Pro Max — 최고화질
    'img_ideogram':   400,   # Ideogram 3.0 — 한글 타이포
    'bg_replace':      80,   # Bria — 누끼컷 배경 교체
    'img_card_news':  800,   # FLUX + PIL 합성 카드뉴스
    'logo':           800,   # Ideogram 로고 시안
    'bg_remove_adv':   20,   # fal.ai BiRefNet 고급 배경 제거
    'image_generation': 30,  # 스튜디오 이미지 생성 (FLUX Schnell, 장당)
}

# ──────────────────────────────────────────
# 블로그 — 분량별 포인트 + 변형 모드 할인
# ──────────────────────────────────────────
BLOG_LENGTH_COSTS = {
    500:  20,    # 짧은 정보형 / 요약
    1000: 40,    # 기본 SEO 블로그
    2000: 80,    # 롱폼 SEO / 심층 가이드
}

# 이전 글과의 관계 모드
RELATION_MODE_OPTIONS = [
    ('new',     '새 주제 — 이전 글과 다르게'),
    ('series',  '시리즈 후속편 — 이전 글 이어서'),
    ('variant', '변형 / 재가공 — 같은 주제 다른 각도 (50% 할인)'),
    ('ignore',  '이력 무시 — 독립적으로 작성'),
]

# 블로그 앵글 (글 각도 — 다양성 축)
BLOG_ANGLE_OPTIONS = [
    ('information', '정보형 — 가이드/설명'),
    ('review',      '후기형 — 사용자 경험'),
    ('timeline',    '시기별 — 월령/계절/단계별'),
    ('comparison',  '비교형 — vs 대안'),
    ('qna',         'Q&A — 자주 묻는 질문'),
    ('trend',       '트렌드 — 최신 이슈/유행'),
]

# 상품 카테고리 (시스템 금지어/디스클레이머 매칭 키)
PRODUCT_CATEGORY_OPTIONS = [
    ('general',           '일반'),
    ('food',              '식품'),
    ('baby_food',         '이유식 / 영유아식품'),
    ('health_supplement', '건강기능식품'),
    ('cosmetics',         '화장품'),
    ('medical_device',    '의료기기'),
    ('lifestyle',         '생활/가전'),
    ('fashion',           '의류/패션'),
]


def get_blog_cost(length: int, relation_mode: str = 'new') -> int:
    """블로그 분량 + 관계 모드별 포인트 비용.

    - 분량 매핑이 없으면 가장 가까운 옵션으로 폴백 (1,000자 기준).
    - relation_mode='variant' 면 50% 할인 (변형/재가공 격려).
    """
    base = BLOG_LENGTH_COSTS.get(int(length) if length else 1000, 40)
    if relation_mode == 'variant':
        return max(1, base // 2)
    return base

CREATION_LABELS = {
    'blog':            '블로그 포스트',
    'instagram':       '인스타 캡션',
    'detail_page':     '상세페이지 카피',
    'thumbnail_text':  '썸네일 문구',
    'ad_copy':         '광고 카피',
    'press_release':   '보도자료',
    'thumbnail_image': '썸네일 이미지',
    'detail_image':    '상세페이지 이미지',
    'card_news':       '인스타 카드뉴스',
    'logo':            '브랜드 로고',
    'brand_package':   '브랜드 정체성 패키지',
    'product_launch':  '상품 런칭 패키지',
}


# ──────────────────────────────────────────
# 사이드바 메뉴
# ──────────────────────────────────────────
MENU_REGISTRY = [
    # (label, icon, endpoint, required_plan_feature, group)
    ('대시보드',    'bi-speedometer2',        'main.dashboard',     None,        '홈'),
    ('브랜드 관리', 'bi-building',            'brand.index',        None,        '홈'),
    ('상품 관리',   'bi-box-seam',            'product.index',      None,        '상품'),
    ('블로그',      'bi-file-text',           'create.blog',        None,        '콘텐츠 생성'),
    ('인스타그램',  'bi-instagram',           'create.instagram',   None,        '콘텐츠 생성'),
    ('상세페이지',  'bi-layout-text-sidebar', 'create.detail_page', None,        '콘텐츠 생성'),
    ('썸네일 문구', 'bi-card-heading',        'create.thumbnail',   None,        '콘텐츠 생성'),
    ('광고 카피',   'bi-megaphone',           'create.ad_copy',     None,        '콘텐츠 생성'),
    ('이미지 생성', 'bi-image',               'create.hub',         'image_gen', '콘텐츠 생성'),
    ('브랜드 키트', 'bi-palette',             'create.brand_kit',   'brand_kit', '콘텐츠 생성'),
    ('생성 이력',   'bi-clock-history',       'main.history',       None,        '관리'),
    ('구독 관리',   'bi-credit-card',         'billing.index',      None,        '관리'),
    ('팀 관리',     'bi-people',              'team.index',         None,        '관리'),  # operator_admin 전용 — get_menu_items() 에서 필터
]


# ──────────────────────────────────────────
# User 모델
# ──────────────────────────────────────────
class User(UserMixin):
    """Flask-Login 호환 사용자 객체 (B2C — operator_id 없음)"""

    def __init__(self, row: dict):
        self.id = str(row.get('id', ''))
        self.email = row.get('email', '')
        self.name = row.get('name', self.email.split('@')[0])
        self.plan_type = row.get('plan_type', 'free')
        self._is_active = row.get('is_active', True)
        self.site_role = row.get('site_role', 'user')  # 'user' | 'operator_admin' | 'superadmin'
        self.operator_id = row.get('operator_id')
        self.subscription_status = row.get('subscription_status', 'trial')
        self.trial_ends_at = row.get('trial_ends_at')
        self.current_period_end = row.get('current_period_end')
        self.created_at = row.get('created_at', '')

    @property
    def is_active(self):
        return self._is_active

    @property
    def is_superadmin(self):
        return self.site_role == 'superadmin'

    @property
    def is_operator_admin(self):
        return self.site_role in ('superadmin', 'operator_admin')

    @property
    def plan_info(self):
        return PLAN_FEATURES.get(self.plan_type, PLAN_FEATURES['free'])

    def has_feature(self, feature: str) -> bool:
        if self.is_superadmin:
            return True
        return bool(self.plan_info.get(feature, False))

    def get_menu_items(self):
        items = []
        for label, icon, endpoint, feature, group in MENU_REGISTRY:
            if feature and not self.has_feature(feature):
                continue
            # 팀 관리: operator admin(기업 관리자)만 표시
            if endpoint == 'team.index' and not (self.operator_id and self.is_operator_admin):
                continue
            items.append({'label': label, 'icon': icon, 'endpoint': endpoint, 'group': group})
        return items


# ──────────────────────────────────────────
# 데코레이터
# ──────────────────────────────────────────
def require_feature(feature):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.has_feature(feature):
                flash(f'이 기능은 상위 플랜에서 사용 가능합니다. (현재: {current_user.plan_type})', 'warning')
                return redirect(url_for('billing.index'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_superadmin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_superadmin:
            flash('접근 권한이 없습니다.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated
