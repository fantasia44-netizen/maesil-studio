"""팀(Operator) 풀 검증 하네스 — point/sub/payment/creation 풀 분기.

검증 대상:
  - migrations/002_blog_enhancements.sql 시드/스키마
  - migrations/003_team_pool.sql 컬럼/인덱스/Backfill
  - models.get_blog_cost(): 분량별 + variant 50% 할인
  - services.regulatory: 카테고리별 시스템 금지어 + 3-tier 합집합 + 디스클레이머
  - services.point_service: _resolve_owner / _scope_filter 동작
                             personal vs operator pool 분기
  - blueprints.billing: _can_manage_subscription 권한 분기
  - blueprints.main: _scoped_creations_query / 그룹 뷰
  - templates/history/index.html: 그룹 마크업
  - auth.py: 초대 멤버 자기 trial 미생성 로직

Supabase 미연결 환경에서도 통과 (인메모리 mock + 순수 함수 단위).
실행:  py -3 -X utf8 test_team_pool_harness.py
"""
from __future__ import annotations

import os
import sys
import json
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = '\033[92m PASS\033[0m'
FAIL = '\033[91m FAIL\033[0m'
results: list[tuple[str, bool]] = []


def check(label, condition, detail=''):
    mark = PASS if condition else FAIL
    print(f'{mark}  {label}' + (f'  [{detail}]' if detail else ''))
    results.append((label, bool(condition)))


# ── 환경 ──────────────────────────────────────────────────
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-key')
os.environ.setdefault('SUPABASE_URL', 'https://placeholder.supabase.co')
os.environ.setdefault('SUPABASE_SERVICE_KEY', 'placeholder')
os.environ.setdefault('ENCRYPTION_KEY', 'a' * 32)


# ──────────────────────────────────────────────────────────
# 1. 마이그레이션 파일 검사
# ──────────────────────────────────────────────────────────
print('\n[1] migrations/002_blog_enhancements.sql')
m2 = os.path.join(ROOT, 'migrations', '002_blog_enhancements.sql')
check('파일 존재', os.path.isfile(m2))
if os.path.isfile(m2):
    sql2 = open(m2, encoding='utf-8').read()
    check('products.avoid_words 컬럼 추가',
          'ADD COLUMN IF NOT EXISTS avoid_words' in sql2)
    check('creations.product_id 컬럼 추가',
          'ADD COLUMN IF NOT EXISTS product_id' in sql2)
    check('creations.angle 컬럼 추가',
          'ADD COLUMN IF NOT EXISTS angle' in sql2)
    check('creations.relation_mode 컬럼 추가',
          'ADD COLUMN IF NOT EXISTS relation_mode' in sql2)
    check('creations.relation_ref_id 컬럼 추가',
          'ADD COLUMN IF NOT EXISTS relation_ref_id' in sql2)
    check('saas_config 카테고리별 금지어 시드 (general)',
          'regulatory_keywords_general' in sql2)
    check('saas_config 식품 금지어 시드',
          'regulatory_keywords_food' in sql2 and '"효능"' in sql2)
    check('saas_config 이유식 금지어 시드',
          'regulatory_keywords_baby_food' in sql2 and '"분유 대용"' in sql2)
    check('saas_config 화장품 금지어 시드',
          'regulatory_keywords_cosmetics' in sql2)
    check('saas_config 디스클레이머 일반/규제 시드',
          'disclaimer_general' in sql2 and 'disclaimer_regulated' in sql2)
    check('saas_config 분량별 비용 시드 (500/1000/2000)',
          'blog_cost_500' in sql2 and 'blog_cost_1000' in sql2
          and 'blog_cost_2000' in sql2)
    check('ON CONFLICT DO NOTHING (멱등)', 'ON CONFLICT (key) DO NOTHING' in sql2)


print('\n[2] migrations/003_team_pool.sql')
m3 = os.path.join(ROOT, 'migrations', '003_team_pool.sql')
check('파일 존재', os.path.isfile(m3))
if os.path.isfile(m3):
    sql3 = open(m3, encoding='utf-8').read()
    check('point_ledger.operator_id 추가',
          'ALTER TABLE point_ledger' in sql3 and 'ADD COLUMN IF NOT EXISTS operator_id' in sql3)
    check('subscriptions.operator_id 추가',
          'ALTER TABLE subscriptions' in sql3)
    check('payments.operator_id 추가',
          'ALTER TABLE payments' in sql3)
    check('creations.operator_id 추가',
          'ALTER TABLE creations' in sql3)
    check('FK ON DELETE CASCADE',
          'REFERENCES operators(id) ON DELETE CASCADE' in sql3)
    check('partial 인덱스 idx_point_ledger_operator',
          'idx_point_ledger_operator' in sql3
          and 'WHERE operator_id IS NOT NULL' in sql3)
    check('Backfill UPDATE point_ledger',
          'UPDATE point_ledger pl' in sql3 and 'SET operator_id = u.operator_id' in sql3)
    check('Backfill UPDATE creations',
          'UPDATE creations c' in sql3)
    check('초대 멤버 trial 정리',
          "u.site_role = 'user'" in sql3 and "s.status = 'trial'" in sql3
          and "status = 'cancelled'" in sql3)
    check('NOTIFY pgrst 스키마 리로드', "NOTIFY pgrst, 'reload schema'" in sql3)


# ──────────────────────────────────────────────────────────
# 3. models.get_blog_cost — 분량 + variant 할인
# ──────────────────────────────────────────────────────────
print('\n[3] models.get_blog_cost')
from models import get_blog_cost, BLOG_LENGTH_COSTS, BLOG_ANGLE_OPTIONS, RELATION_MODE_OPTIONS

check('500자 = 20P',  get_blog_cost(500) == 20)
check('1000자 = 40P', get_blog_cost(1000) == 40)
check('2000자 = 80P', get_blog_cost(2000) == 80)
check('알 수 없는 분량 → 1000자 폴백 40P', get_blog_cost(777) == 40)
check('variant 모드 50% 할인 (1000자 → 20P)',
      get_blog_cost(1000, 'variant') == 20)
check('variant 모드 (2000자 → 40P)',
      get_blog_cost(2000, 'variant') == 40)
check('variant 모드 (500자 → 10P)',
      get_blog_cost(500, 'variant') == 10)
check('new 모드는 풀 비용', get_blog_cost(1000, 'new') == 40)
check('series 모드는 풀 비용', get_blog_cost(2000, 'series') == 80)
check('ignore 모드는 풀 비용', get_blog_cost(500, 'ignore') == 20)
check('BLOG_LENGTH_COSTS 키 3개 (500/1000/2000)',
      sorted(BLOG_LENGTH_COSTS.keys()) == [500, 1000, 2000])
check('BLOG_ANGLE_OPTIONS 6종', len(BLOG_ANGLE_OPTIONS) == 6)
check('RELATION_MODE_OPTIONS 4종', len(RELATION_MODE_OPTIONS) == 4)
check('관계 모드 키 정확',
      {k for k, _ in RELATION_MODE_OPTIONS} == {'new', 'series', 'variant', 'ignore'})


# ──────────────────────────────────────────────────────────
# 4. services.regulatory — 시스템/3-tier/디스클레이머
# ──────────────────────────────────────────────────────────
print('\n[4] services.regulatory')

# Flask app + 가짜 supabase 컨텍스트
from flask import Flask
test_app = Flask(__name__)
test_app.config['TESTING'] = True


class _FakeSupabase:
    """saas_config 조회만 모킹하는 최소 supabase."""

    def __init__(self, config_map: dict[str, str]):
        self.config = config_map
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []

    def table(self, name):
        return _FakeTable(self, name)


class _FakeTable:
    def __init__(self, sb: _FakeSupabase, name: str):
        self.sb = sb
        self.name = name
        self._filters: list[tuple[str, str, object]] = []
        self._select_cols = '*'
        self._order = None
        self._limit_n = None
        self._action = None
        self._payload = None

    def select(self, cols, **kw):
        self._select_cols = cols
        self._action = 'select'
        return self

    def insert(self, payload):
        self._action = 'insert'
        self._payload = payload
        self.sb.inserts.append((self.name, payload))
        return self

    def update(self, payload):
        self._action = 'update'
        self._payload = payload
        self.sb.updates.append((self.name, payload))
        return self

    def eq(self, col, val):
        self._filters.append(('eq', col, val))
        return self

    def is_(self, col, val):
        self._filters.append(('is', col, val))
        return self

    def order(self, *a, **kw):
        self._order = (a, kw)
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def range(self, *a, **kw):
        return self

    def execute(self):
        if self.name == 'saas_config' and self._action == 'select':
            # key eq 매칭
            for kind, col, val in self._filters:
                if col == 'key':
                    text = self.sb.config.get(val, '')
                    if text:
                        return MagicMock(data=[{'value_text': text}])
                    return MagicMock(data=[])
            return MagicMock(data=[])
        if self.name == 'point_ledger' and self._action == 'select':
            # 잔액 조회 — 마지막 매칭 행의 balance 반환
            return MagicMock(data=_LEDGER_QUERY(self.sb, self._filters))
        if self.name == 'users' and self._action == 'select':
            uid = next((v for k, c, v in self._filters if c == 'id'), None)
            row = self.sb.users.get(uid)  # type: ignore[attr-defined]
            return MagicMock(data=[row] if row else [])
        return MagicMock(data=[])


def _LEDGER_QUERY(sb: _FakeSupabase, filters):
    """ledger 테이블 검색 (operator_id 또는 user_id + IS NULL)."""
    rows = getattr(sb, 'ledger_rows', [])
    op_filter = next((v for k, c, v in filters if k == 'eq' and c == 'operator_id'), None)
    user_filter = next((v for k, c, v in filters if k == 'eq' and c == 'user_id'), None)
    is_null_op = any(k == 'is' and c == 'operator_id' and v == 'null' for k, c, v in filters)

    matched = []
    for r in rows:
        if op_filter is not None:
            if r.get('operator_id') == op_filter:
                matched.append(r)
        elif is_null_op and user_filter is not None:
            if not r.get('operator_id') and r.get('user_id') == user_filter:
                matched.append(r)
    matched.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return matched[:1] if matched else []


CONFIG_SEED = {
    'regulatory_keywords_general': '["100%","무조건","최고"]',
    'regulatory_keywords_food': '["효능","치료","면역력","항암"]',
    'regulatory_keywords_baby_food': '["분유 대용","두뇌발달"]',
    'regulatory_keywords_cosmetics': '["여드름 치료","주름 제거"]',
    'disclaimer_general': '⚠️ AI 생성 콘텐츠 안내\n게시 전 검토하세요.',
    'disclaimer_regulated': '⚠️ 광고·표시 규정 안내\n효능/치료 표현 제한.',
    'disclaimer_category_map': '{"food":"regulated","baby_food":"regulated","health_supplement":"regulated","cosmetics":"regulated","medical_device":"regulated","general":"general"}',
}

with test_app.app_context():
    test_app.supabase = _FakeSupabase(CONFIG_SEED)

    from services.regulatory import (
        get_system_avoid_words, combine_avoid_words,
        get_disclaimer, append_disclaimer, scan_violations,
    )

    food_words = get_system_avoid_words('food')
    check('food 카테고리 → 일반+식품 합집합',
          '효능' in food_words and '치료' in food_words and '100%' in food_words)
    check('food 중복 제거 정상', food_words.count('100%') == 1)

    baby = get_system_avoid_words('baby_food')
    check('baby_food → 일반+이유식 합집합',
          '분유 대용' in baby and '두뇌발달' in baby and '100%' in baby)

    general = get_system_avoid_words('general')
    check('general → 일반만',
          '100%' in general and '효능' not in general)

    unknown = get_system_avoid_words('unknown_category')
    check('알 수 없는 카테고리 → general 폴백',
          '100%' in unknown and '효능' not in unknown)

    # 3-tier 합집합
    brand = {'avoid_words': ['브랜드 금지1', '효능']}  # 효능: 시스템과 중복
    product = {'avoid_words': ['상품 금지1', '두뇌발달']}
    merged = combine_avoid_words(brand, product, 'baby_food')
    check('3-tier: 시스템+브랜드+상품 모두 포함',
          '효능' in merged and '브랜드 금지1' in merged
          and '상품 금지1' in merged and '두뇌발달' in merged
          and '100%' in merged)
    check('3-tier 중복 제거', merged.count('효능') == 1 and merged.count('두뇌발달') == 1)

    # 디스클레이머
    d_food = get_disclaimer('food')
    check('food 디스클레이머 = regulated 본문',
          '광고·표시 규정' in d_food)

    d_general = get_disclaimer('general')
    check('general 디스클레이머 = general 본문',
          '게시 전 검토' in d_general)

    d_unknown = get_disclaimer('cosmetics')
    check('매핑 없는 cosmetics → 폴백 regulated',
          '광고·표시 규정' in d_unknown)

    appended = append_disclaimer('블로그 본문 텍스트', 'food')
    check('append_disclaimer: 본문 + 구분선 + 디스클레이머',
          '블로그 본문 텍스트' in appended and '광고·표시 규정' in appended
          and '\n---\n' in appended)

    appended_twice = append_disclaimer(appended, 'food')
    check('append_disclaimer: 중복 부착 방지',
          appended_twice.count('광고·표시 규정') == 1)

    # 위반 스캔
    text = '이 제품은 면역력을 높이고 효능이 뛰어납니다. 100% 안전합니다.'
    found = scan_violations(text, ['효능', '면역력', '치료', '100%', 'NotInText'])
    check('scan_violations: 검출됨',
          '효능' in found and '면역력' in found and '100%' in found)
    check('scan_violations: 미존재 단어 제외',
          '치료' not in found and 'NotInText' not in found)
    check('scan_violations: 빈 입력 안전',
          scan_violations('', ['효능']) == [] and scan_violations('text', []) == [])


# ──────────────────────────────────────────────────────────
# 5. services.point_service._resolve_owner — owner 분기
# ──────────────────────────────────────────────────────────
print('\n[5] services.point_service — owner 분기')

with test_app.app_context():
    test_app.supabase = _FakeSupabase(CONFIG_SEED)
    test_app.supabase.users = {  # type: ignore[attr-defined]
        'u-personal':  {'operator_id': None},
        'u-admin':     {'operator_id': 'op-1'},
        'u-member':    {'operator_id': 'op-1'},
    }

    from services.point_service import _resolve_owner

    # User 객체
    class _U:
        def __init__(self, id, op=None):
            self.id = id
            self.operator_id = op
            self.is_authenticated = True

    uid, op = _resolve_owner(_U('u-personal', None))
    check('User 객체(개인) → user_id, operator_id=None',
          uid == 'u-personal' and op is None)

    uid, op = _resolve_owner(_U('u-admin', 'op-1'))
    check('User 객체(팀) → user_id + operator_id',
          uid == 'u-admin' and op == 'op-1')

    # dict
    uid, op = _resolve_owner({'id': 'u-x', 'operator_id': 'op-2'})
    check('dict 입력 정상', uid == 'u-x' and op == 'op-2')

    # str (DB 조회)
    uid, op = _resolve_owner('u-personal')
    check('str user_id (개인) → operator_id=None', uid == 'u-personal' and op is None)

    uid, op = _resolve_owner('u-admin')
    check('str user_id (팀) → operator_id 자동 추론', uid == 'u-admin' and op == 'op-1')

    uid, op = _resolve_owner('')
    check('빈 입력 → 빈 user_id', uid == '' and op is None)


# ──────────────────────────────────────────────────────────
# 6. services.point_service.get_balance — 풀별 잔액
# ──────────────────────────────────────────────────────────
print('\n[6] services.point_service.get_balance — 풀 분기')

with test_app.app_context():
    sb = _FakeSupabase(CONFIG_SEED)
    sb.users = {  # type: ignore[attr-defined]
        'u-personal': {'operator_id': None},
        'u-admin':    {'operator_id': 'op-1'},
        'u-member':   {'operator_id': 'op-1'},
    }
    sb.ledger_rows = [  # type: ignore[attr-defined]
        # 개인 풀 — operator_id 없음
        {'user_id': 'u-personal', 'operator_id': None,
         'balance': 500, 'created_at': '2026-04-01T10:00:00'},
        # 팀 풀 (op-1) — admin 이 충전
        {'user_id': 'u-admin', 'operator_id': 'op-1',
         'balance': 3000, 'created_at': '2026-04-15T10:00:00'},
        # 팀 풀 — member 가 사용 (operator_id 같음)
        {'user_id': 'u-member', 'operator_id': 'op-1',
         'balance': 2960, 'created_at': '2026-04-20T10:00:00'},
    ]
    test_app.supabase = sb

    from services.point_service import get_balance

    class _U:
        def __init__(self, id, op=None):
            self.id, self.operator_id, self.is_authenticated = id, op, True

    # 개인
    bal = get_balance(_U('u-personal'))
    check('개인 사용자 잔액 = user 풀 (500)', bal == 500)

    # 팀 admin
    bal_admin = get_balance(_U('u-admin', 'op-1'))
    check('팀 admin 잔액 = operator 풀 최신 (2960)', bal_admin == 2960)

    # 팀 member 가 admin과 동일한 잔액 보여야 함
    bal_member = get_balance(_U('u-member', 'op-1'))
    check('팀 member 잔액 = operator 풀 (admin과 동일 2960)',
          bal_member == bal_admin == 2960)

    # 다른 operator 의 잔액은 분리
    bal_other = get_balance(_U('u-other', 'op-2'))
    check('다른 operator 잔액 = 0 (격리됨)', bal_other == 0)


# ──────────────────────────────────────────────────────────
# 7. point_service.use_points / add_points — operator_id 행 기록
# ──────────────────────────────────────────────────────────
print('\n[7] point_service.use_points / add_points — operator_id 같이 기록')

with test_app.app_context():
    sb = _FakeSupabase(CONFIG_SEED)
    sb.users = {'u-admin': {'operator_id': 'op-1'},
                'u-member': {'operator_id': 'op-1'}}
    sb.ledger_rows = [
        {'user_id': 'u-admin', 'operator_id': 'op-1',
         'balance': 1000, 'created_at': '2026-04-15T10:00:00'},
    ]
    test_app.supabase = sb

    from services.point_service import use_points, add_points

    class _U:
        def __init__(self, id, op=None):
            self.id, self.operator_id, self.is_authenticated = id, op, True

    # member 가 80P 차감 시도
    new_bal = use_points(_U('u-member', 'op-1'), 'blog', 'creation-1',
                         cost_override=80, note_override='블로그 (1,000자)')
    check('member 차감 후 잔액 = 920', new_bal == 920)

    inserted = sb.inserts[-1]
    check('insert 테이블 = point_ledger', inserted[0] == 'point_ledger')
    payload = inserted[1]
    check('차감 행에 operator_id 기록됨', payload.get('operator_id') == 'op-1')
    check('차감 행에 user_id (member) 도 기록됨 (감사용)',
          payload.get('user_id') == 'u-member')
    check('차감 amount 음수 (-80)', payload.get('amount') == -80)
    check('차감 type=use', payload.get('type') == 'use')
    check('차감 note=ledger_note', payload.get('note') == '블로그 (1,000자)')

    # admin 이 충전
    add_points(_U('u-admin', 'op-1'), 3000, 'subscription_grant',
               ref_id='pay-x', note='Starter 구독 포인트 지급')
    inserted = sb.inserts[-1]
    payload = inserted[1]
    check('충전 행에 operator_id', payload.get('operator_id') == 'op-1')
    check('충전 amount 양수 (+3000)', payload.get('amount') == 3000)


# ──────────────────────────────────────────────────────────
# 8. blueprints.billing — 권한 분기 + scoped 쿼리
# ──────────────────────────────────────────────────────────
print('\n[8] blueprints.billing')

# 직접 임포트는 Flask context 밖 → 모듈 임포트만 검증
import importlib
billing_mod = importlib.import_module('blueprints.billing')
check('billing.py 임포트', hasattr(billing_mod, 'billing_bp'))
check('_can_manage_subscription 정의됨',
      callable(getattr(billing_mod, '_can_manage_subscription', None)))
check('_scoped_subscription_query 정의됨',
      callable(getattr(billing_mod, '_scoped_subscription_query', None)))
check('_scoped_payments_query 정의됨',
      callable(getattr(billing_mod, '_scoped_payments_query', None)))

# 권한 분기 — current_user 모킹
from flask_login import current_user as _cu

class _MockUser:
    def __init__(self, op_id, role):
        self.id = 'u-test'
        self.operator_id = op_id
        self.site_role = role
    @property
    def is_operator_admin(self):
        return self.site_role in ('superadmin', 'operator_admin')
    @property
    def is_superadmin(self):
        return self.site_role == 'superadmin'

with test_app.app_context():
    with patch('blueprints.billing.current_user', _MockUser(None, 'user')):
        check('개인 사용자(operator_id 없음) → 결제 가능',
              billing_mod._can_manage_subscription() is True)
    with patch('blueprints.billing.current_user', _MockUser('op-1', 'operator_admin')):
        check('operator_admin → 결제 가능',
              billing_mod._can_manage_subscription() is True)
    with patch('blueprints.billing.current_user', _MockUser('op-1', 'superadmin')):
        check('superadmin → 결제 가능',
              billing_mod._can_manage_subscription() is True)
    with patch('blueprints.billing.current_user', _MockUser('op-1', 'user')):
        check('일반 팀 멤버 → 결제 불가 (False)',
              billing_mod._can_manage_subscription() is False)


# ──────────────────────────────────────────────────────────
# 9. blueprints.main — scoped 쿼리 + 그룹 뷰
# ──────────────────────────────────────────────────────────
print('\n[9] blueprints.main — scoped 쿼리 + 그룹 뷰')

main_mod = importlib.import_module('blueprints.main')
check('main.py 임포트', hasattr(main_mod, 'main_bp'))
check('_scoped_creations_query 정의됨',
      callable(getattr(main_mod, '_scoped_creations_query', None)))
check('_scoped_brands_query 정의됨',
      callable(getattr(main_mod, '_scoped_brands_query', None)))


# ──────────────────────────────────────────────────────────
# 10. templates/history/index.html — 그룹 뷰 마크업
# ──────────────────────────────────────────────────────────
print('\n[10] templates/history/index.html')

tpl = os.path.join(ROOT, 'templates', 'history', 'index.html')
check('파일 존재', os.path.isfile(tpl))
if os.path.isfile(tpl):
    html = open(tpl, encoding='utf-8').read()
    check('TYPE_META 정의 (유형별 컬러/아이콘)', 'TYPE_META' in html)
    check('blog 메타 정의', "'blog'" in html and "bi-file-text" in html)
    check('instagram 메타 정의', "'instagram'" in html and 'bi-instagram' in html)
    check('detail_page 메타 정의', "'detail_page'" in html)
    check('thumbnail_image 메타 정의', "'thumbnail_image'" in html)
    check('그룹 뷰 분기 (view_mode == grouped)', "view_mode == 'grouped'" in html)
    check('평면 뷰 토글 링크 (?view=flat)', '?view=flat' in html)
    check('유형별 카운트 뱃지', '{{ g.count }}' in html)
    check('전체 보기 링크 (g.has_more)', 'g.has_more' in html)
    check('팀 모드 배너', 'is_team_mode' in html and '팀 공유' in html)


# ──────────────────────────────────────────────────────────
# 11. auth.py — 초대 멤버 trial 미생성
# ──────────────────────────────────────────────────────────
print('\n[11] auth.py — 초대 멤버 trial 분기')

auth_path = os.path.join(ROOT, 'auth.py')
auth_src = open(auth_path, encoding='utf-8').read()
check('is_invited_member 분기 존재', 'is_invited_member' in auth_src)
check("biz_sub == 'invited' 매칭", "biz_sub == 'invited'" in auth_src)
check('초대 멤버 → subscription insert 스킵',
      'if not is_invited_member' in auth_src)
check('신규 operator 관리자 trial 에 operator_id 매핑',
      "sub_row['operator_id'] = new_op_id" in auth_src)


# ──────────────────────────────────────────────────────────
# 12. blueprints.create.blog — 4축 + 관계 모드 + 비용
# ──────────────────────────────────────────────────────────
print('\n[12] blueprints.create.blog — 4축 + 관계 모드')

blog_mod = importlib.import_module('blueprints.create.blog')
check('blog.py 임포트', hasattr(blog_mod, 'blog'))
check('blog_generate 라우트 정의', hasattr(blog_mod, 'blog_generate'))
check('blog_products (브랜드 변경 AJAX)', hasattr(blog_mod, 'blog_products'))
check('_detect_category 정의', callable(getattr(blog_mod, '_detect_category', None)))
check('_recent_blog_creations 정의',
      callable(getattr(blog_mod, '_recent_blog_creations', None)))
check('_related_creation_payload 정의',
      callable(getattr(blog_mod, '_related_creation_payload', None)))

# 카테고리 정규화
norm = blog_mod._normalize_category
check('카테고리 정규화 — 이유식', norm('이유식') == 'baby_food')
check('카테고리 정규화 — 영유아', norm('영유아') == 'baby_food')
check('카테고리 정규화 — 식품', norm('식품') == 'food')
check('카테고리 정규화 — 화장품', norm('화장품') == 'cosmetics')
check('카테고리 정규화 — 빈 입력', norm('') == '')
check('카테고리 정규화 — 알 수 없음 → 그대로 lower',
      norm('Unknown') == 'unknown')

# 카테고리 감지 우선순위
detect = blog_mod._detect_category
check('상품 카테고리 우선 (상품: 이유식, 브랜드: 식품 → baby_food)',
      detect({'industry': '식품'}, {'category': '이유식'}) == 'baby_food')
check('상품 없으면 브랜드 업종 사용',
      detect({'industry': '화장품'}, None) == 'cosmetics')
check('둘 다 없으면 general',
      detect({}, None) == 'general')


# ──────────────────────────────────────────────────────────
# 13. services.prompts.blog — 4축 입력 + 관계 모드
# ──────────────────────────────────────────────────────────
print('\n[13] services.prompts.blog — 프롬프트 빌드')

with test_app.app_context():
    test_app.supabase = _FakeSupabase(CONFIG_SEED)

    from services.prompts.blog import build_prompt

    brand = {'name': '배마마', 'industry': '이유식', 'target_customer': '30대 엄마'}
    product = {'name': '야채큐브', 'category': '이유식', 'price': 18900,
               'features': ['첨가물 무첨가', '월령별']}

    # new 모드
    sys_p, user_p, max_t = build_prompt(
        brand,
        {'topic': '이유식재료', 'keyword': '야채큐브', 'angle': 'information',
         'length': '1000', 'relation_mode': 'new'},
        product=product,
        merged_avoid_words=['효능', '치료'],
        recent_creations=[
            {'title': '이전 글 1', 'topic': '이유식', 'keyword': '죽', 'angle': 'review'},
        ],
    )
    check('build_prompt new 모드 → tuple 반환', isinstance((sys_p, user_p, max_t), tuple))
    check('1000자 max_tokens=4000', max_t == 4000)
    check('system 프롬프트에 브랜드명', '배마마' in sys_p)
    check('system 프롬프트에 상품 정보', '야채큐브' in sys_p)
    check('system 프롬프트에 가격 18,900원 포맷', '18,900원' in sys_p)
    check('system 프롬프트에 금지 표현 강조',
          '절대 사용 금지' in sys_p and '효능' in sys_p and '치료' in sys_p)
    check('user 프롬프트 — 이력 회피 모드',
          '다양성 모드' in user_p and '이전 글 1' in user_p)
    check('user 프롬프트 — 출력 형식 강제',
          '제목 후보 (3개)' in user_p and '메타 디스크립션' in user_p)

    # series 모드
    sys_s, user_s, _ = build_prompt(
        brand,
        {'topic': '이유식재료', 'keyword': '야채큐브', 'angle': 'timeline',
         'length': '2000', 'relation_mode': 'series'},
        related_creation={'title': '시리즈 1편 — 이유식 입문',
                          'excerpt': '이전 글 본문 발췌입니다.'},
    )
    check('series 모드 max_tokens=6000', _ == 6000)
    check('series 모드 — 후속편 지시 포함',
          '시리즈 후속편' in user_s and '시리즈 1편' in user_s)

    # variant 모드
    _, user_v, _ = build_prompt(
        brand,
        {'topic': '이유식재료', 'keyword': '야채큐브', 'angle': 'comparison',
         'length': '500', 'relation_mode': 'variant'},
        related_creation={'title': '원본', 'excerpt': '원본 본문'},
    )
    check('variant 모드 — 재가공 지시 포함',
          '변형/재가공 모드' in user_v and '주제·핵심 메시지는 동일' in user_v)

    # 글 목적 분기
    sys_purchase, _, _ = build_prompt(
        brand,
        {'topic': 't', 'keyword': 'k', 'purpose': '구매유도',
         'angle': 'review', 'length': '1000', 'relation_mode': 'ignore'},
    )
    check('구매유도 → 전환 중심 지시', '전환 중심' in sys_purchase)

    sys_brand, _, _ = build_prompt(
        brand,
        {'topic': 't', 'keyword': 'k', 'purpose': '브랜드인지',
         'angle': 'review', 'length': '1000', 'relation_mode': 'ignore'},
    )
    check('브랜드인지 → 가치/관점/철학 지시',
          '가치·관점·철학' in sys_brand or '브랜드 스토리' in sys_brand)

    sys_info, _, _ = build_prompt(
        brand,
        {'topic': 't', 'keyword': 'k', 'purpose': '정보제공',
         'angle': 'information', 'length': '1000', 'relation_mode': 'ignore'},
    )
    check('정보제공 → 정보 비중 강조',
          '정보 80%' in sys_info or '검색 의도' in sys_info)


# ──────────────────────────────────────────────────────────
# 14. services.claude_service.build_brand_context 확장
# ──────────────────────────────────────────────────────────
print('\n[14] services.claude_service.build_brand_context 확장')

from services.claude_service import build_brand_context

ctx_simple = build_brand_context({'name': '배마마'})
check('단일 브랜드 컨텍스트', '배마마' in ctx_simple)

ctx_with_product = build_brand_context(
    {'name': '배마마', 'industry': '이유식'},
    product={'name': '야채큐브', 'price': 18900, 'features': ['무첨가', '월령별']},
)
check('상품 컨텍스트 주입 — 상품명',
      '야채큐브' in ctx_with_product)
check('상품 컨텍스트 — 가격 포맷',
      '18,900원' in ctx_with_product)
check('상품 컨텍스트 — 핵심 특징',
      '무첨가' in ctx_with_product and '월령별' in ctx_with_product)

ctx_avoids = build_brand_context(
    {'name': '배마마'},
    merged_avoid_words=['효능', '치료', '면역력'],
)
check('merged_avoid_words 주입 시 강조 문구',
      '절대 사용 금지' in ctx_avoids and '효능' in ctx_avoids)

ctx_legacy = build_brand_context({'name': '배마마', 'avoid_words': ['옛 단어']})
check('레거시 호환 — brand.avoid_words 만 있어도 표시됨',
      '옛 단어' in ctx_legacy)


# ──────────────────────────────────────────────────────────
# 15. _base.py run_text_generation 시그니처 확장
# ──────────────────────────────────────────────────────────
print('\n[15] blueprints/create/_base.py')

base_mod = importlib.import_module('blueprints.create._base')
import inspect
sig = inspect.signature(base_mod.run_text_generation)
params = sig.parameters
check('run_text_generation 시그니처에 point_cost', 'point_cost' in params)
check('run_text_generation 시그니처에 ledger_note', 'ledger_note' in params)
check('run_text_generation 시그니처에 extra_creation_fields',
      'extra_creation_fields' in params)
check('run_text_generation 시그니처에 post_process', 'post_process' in params)
check('run_text_generation 시그니처에 max_tokens', 'max_tokens' in params)


# ──────────────────────────────────────────────────────────
# 결과 요약
# ──────────────────────────────────────────────────────────
print('\n' + '═' * 60)
total = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed
print(f'\n총 {total}건 — \033[92mPASS {passed}\033[0m / \033[91mFAIL {failed}\033[0m')

if failed:
    print('\n실패 항목:')
    for label, ok in results:
        if not ok:
            print(f'  - {label}')
    sys.exit(1)
else:
    print('\n모든 시뮬레이션 통과 ✓')
    sys.exit(0)
