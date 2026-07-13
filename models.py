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
        'monthly_points': 22000,
        'brand_profiles': 5,
        'image_gen': True,
        'brand_kit': True,
    },
    'enterprise': {
        'label': 'Enterprise',
        'price': 99000,
        'monthly_points': 50000,
        'brand_profiles': 10,
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
    'experience_blog': 150,  # 경험담 블로그 — 실사진 vision 분석 + 글 정리

    # 이미지 생성 — FLUX Schnell + Ideogram만 운영
    'img_preview':     50,   # FLUX Schnell — 빠른 라이프스타일 씬
    'img_dev':         80,   # FLUX dev — 인체/손 정확도↑ (본문 이미지 기본)
    'img_ideogram':   200,   # Ideogram 3.0 — 한글 타이포 (원가 ~110원)
    'bg_replace':      80,   # Bria — 누끼컷 배경 교체 (원가 ~55원)
    'img_card_news':  800,   # FLUX + PIL 합성 카드뉴스
    'logo':           200,   # Ideogram 로고 시안 3종 (1회 생성)
    'bg_remove_adv':   10,   # fal.ai BiRefNet 고급 배경 제거
    'image_generation': 30,  # 스튜디오 이미지 생성 (FLUX Schnell, 장당)
    # 쇼츠/릴스 영상 (대본+이미지+TTS+조립 통합)
    'shorts_video':     300,  # FLUX 5장 + TTS + FFmpeg 조립 all-in-one
    'shorts_video_kling': 1500, # Kling image2video 3씬 라스트프레임 체이닝 + TTS + FFmpeg
    # 홍보 자료 (제안서·카탈로그·리플릿·전단지)
    'business_proposal':   150,  # 거래처 제안서
    'sponsorship_proposal':150,  # 협찬 제안서
    'catalog':             150,  # 카탈로그 8p 기본 (printout.py에서 분량별 override)
    'leaflet':             120,  # 리플릿 (3단 접이 6패널)
    'flyer':                80,  # 전단지
    # 상세페이지 빌더 — 블록 단위 생성
    'dp_block_text':        30,  # 블록 텍스트 AI 생성 (블록 1개)
    'dp_block_image':       50,  # 블록 이미지 FLUX Schnell 생성
    'dp_bg_replace':        80,  # 블록 이미지 배경 교체 (Bria)
    'dp_flux_text':        300,  # FLUX 배경 + PIL 텍스트 오버레이
    # 상세페이지 초안 제안서
    'detail_page_plan':    150,  # Claude 3타입 기획서 생성
    'detail_page_draft_image': 50,  # 섹션 스케치 이미지 (FLUX Schnell, 장당)
    # 상세페이지 이미지 세트 — 스토리 빌더 섹션별
    'detail_page_image':   250,  # 스토리 섹션 이미지 (PIL 합성, 실제 비용은 섹션별 override)
    # 배너 이미지
    'banner':               80,  # FLUX Schnell 배경 + PIL 합성 (장당)
    'banner_product':       80,  # 상품 사진 Bria 배경 교체 + PIL 합성
    'banner_text':          20,  # PIL만으로 즉시 생성 (텍스트 배너)
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

# ──────────────────────────────────────────
# 생성 유형별 모델 지정
# ──────────────────────────────────────────
_HAIKU  = 'claude-haiku-4-5-20251001'
_SONNET = 'claude-sonnet-4-6'

CREATION_MODELS = {
    # Haiku — 단순 구조형 텍스트
    'blog':           _HAIKU,
    'instagram':      _HAIKU,
    'thumbnail_text': _HAIKU,
    'ad_copy':        _HAIKU,
    'banner_copy':    _HAIKU,
    # Sonnet — 고품질 / 설득형 문서
    'detail_page':          _SONNET,
    'press_release':        _SONNET,
    'shorts_script':        _SONNET,
    'business_proposal':    _SONNET,
    'sponsorship_proposal': _SONNET,
    'catalog':              _SONNET,
    'leaflet':              _SONNET,
    'flyer':                _SONNET,
}

CREATION_LABELS = {
    # 텍스트
    'blog':            '블로그 포스트',
    'experience_blog': '경험담 블로그',
    'instagram':       '인스타 캡션',
    'detail_page':     '상세페이지 카피',
    'thumbnail_text':  '썸네일 문구',
    'ad_copy':         '광고 카피',
    'press_release':   '보도자료',
    # 이미지
    'img_preview':      '이미지 생성',
    'img_dev':          '이미지 생성',
    'img_ideogram':     '이미지 생성 (한글)',
    'bg_replace':       '배경 교체',
    'bg_remove_adv':    '배경 제거',
    'img_card_news':    '인스타 카드뉴스',
    'image_generation': '이미지 생성',
    'thumbnail_image':  '썸네일 이미지',
    'blog_thumbnail':   '블로그 썸네일',
    'detail_image':     '상세페이지 이미지',
    'detail_page_image':'상세페이지 이미지 세트',
    'detail_page_plan': '상세페이지 기획 (3타입)',
    'detail_page_draft':'상세페이지 초안 제안서',
    'detail_page_draft_image': '초안 섹션 이미지',
    'card_news':        '인스타 카드뉴스',
    'logo':             '브랜드 로고',
    'banner':           'AI 배너 이미지',
    'banner_product':   '상품 배너 이미지',
    'banner_text':      '텍스트 배너 이미지',
    # 영상
    'shorts_script':        '쇼츠 대본',
    'shorts_video':         '쇼츠/릴스 영상',
    'shorts_video_kling':   '쇼츠/릴스 영상 (Kling 모션)',
    # 홍보 자료
    'business_proposal':    '거래처 제안서',
    'sponsorship_proposal': '협찬 제안서',
    'catalog':              '카탈로그',
    'leaflet':              '리플릿',
    'flyer':                '전단지',
    'brand_package':        '브랜드 패키지',
    'product_launch':       '신제품 출시 자료',
}


# ──────────────────────────────────────────
# 사이드바 메뉴
# ──────────────────────────────────────────
MENU_REGISTRY = [
    # (label, icon, endpoint, required_plan_feature, group)
    ('대시보드',    'bi-speedometer2',        'main.dashboard',       None,  '홈'),
    ('브랜드 관리', 'bi-building',            'brand.index',          None,  '홈'),
    ('브랜드 로고', 'bi-pentagon',            'create.logo',          None,  '홈'),
    ('상품 관리',   'bi-box-seam',            'product.index',        None,  '상품'),
    # ── 콘텐츠 생성
    ('블로그',      'bi-file-text',           'create.blog',          None,  '콘텐츠 생성'),
    ('경험담 블로그', 'bi-camera',            'create.experience',    None,  '콘텐츠 생성'),
    ('인스타그램',  'bi-instagram',           'create.instagram',     None,  '콘텐츠 생성'),
    ('쇼츠/릴스',   'bi-play-circle',         'create.shorts',        None,  '콘텐츠 생성'),
    ('상세페이지 초안', 'bi-layout-text-sidebar', 'create.detail_page',         None, '콘텐츠 생성'),
    # ── 홍보물
    ('배너 만들기',   'bi-images',             'create.banner',    None, '홍보물'),
    ('홍보물 만들기', 'bi-megaphone',          'create.promo',     None, '홍보물'),
    # ── 관리
    ('생성 이력',   'bi-clock-history',       'main.history',         None,  '관리'),
    ('구독 관리',   'bi-credit-card',         'billing.index',        None,  '관리'),
    ('팀 관리',     'bi-people',              'team.index',           None,  '관리'),
    ('외부 연동',   'bi-plug',                'integrations.index',   None,  '관리'),
    # ── 블라인드 (미사용 — 라우트는 유지)
    # ('썸네일 문구', 'bi-card-heading',        'create.thumbnail',     None,  '콘텐츠 생성'),
    # ('광고 카피',   'bi-megaphone',           'create.ad_copy',       None,  '콘텐츠 생성'),
    # ('이미지 생성', 'bi-image',               'create.hub',           'image_gen', '콘텐츠 생성'),
    # ('브랜드 키트', 'bi-palette',             'create.brand_kit',     'brand_kit', '콘텐츠 생성'),
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
        self.last_seen_at = row.get('last_seen_at')
        self.failed_login_count = row.get('failed_login_count', 0)
        self.locked_until = row.get('locked_until')
        self._view_as_mode = False

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
