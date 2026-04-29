"""매실 크리에이터 - 사용자 모델 + 요금제 + 포인트 비용"""
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
    'blog':            80,
    'instagram':       30,
    'detail_page':    150,
    'thumbnail_text':  40,
    'ad_copy':         60,
    'press_release':  200,
    'brand_package': 1500,
    'product_launch': 2000,
    # 이미지 생성 — 엔진별
    'img_preview':     50,   # FLUX Schnell/Klein — 빠른 시안
    'img_standard':   300,   # FLUX Pro — 브랜드 에셋
    'img_hq':         600,   # FLUX Pro Max — 최고화질
    'img_ideogram':   400,   # Ideogram 3.0 — 한글 타이포
    'img_card_news':  800,   # FLUX + PIL 합성 카드뉴스
    'logo':           800,   # Ideogram 로고 시안
}

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
