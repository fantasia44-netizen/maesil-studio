"""팀(Operator) 풀 검증 하네스 — point/sub/payment/creation/brand/insight 풀 분기.

검증 대상:
  - migrations/002~005 스키마/시드
  - models.get_blog_cost(): 분량별 + variant 50% 할인
  - services.regulatory: 카테고리별 시스템 금지어 + 3-tier 합집합 + 디스클레이머
  - services.point_service: _resolve_owner / _scope_filter 동작
                             personal vs operator pool 분기
  - services.maesil_insight_connection: operator 공유 연결 조회/저장/해제
  - blueprints.billing: _can_manage_subscription 권한 분기
  - blueprints.main: _scoped_creations_query / _get_brand_count / history_detail
  - blueprints.integrations: _can_manage / _op_id
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
# 공통 Mock
# ──────────────────────────────────────────────────────────
class _FakeSupabase:
    """saas_config / ledger / users 조회 모킹."""

    def __init__(self, config_map: dict[str, str]):
        self.config = config_map
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, list]] = []
        self.upserts: list[tuple[str, dict]] = []

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
        self._conflict = None

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

    def upsert(self, payload, on_conflict=None):
        self._action = 'upsert'
        self._payload = payload
        self._conflict = on_conflict
        self.sb.upserts.append((self.name, payload, on_conflict))
        return self

    def delete(self):
        self._action = 'delete'
        return self

    def eq(self, col, val):
        self._filters.append(('eq', col, val))
        return self

    def is_(self, col, val):
        self._filters.append(('is', col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(('in', col, vals))
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
        if self._action == 'delete':
            self.sb.deletes.append((self.name, self._filters))
            return MagicMock(data=[])

        if self.name == 'saas_config' and self._action == 'select':
            for kind, col, val in self._filters:
                if col == 'key':
                    text = self.sb.config.get(val, '')
                    if text:
                        return MagicMock(data=[{'value_text': text}])
                    return MagicMock(data=[])
            return MagicMock(data=[])

        if self.name == 'point_ledger' and self._action == 'select':
            return MagicMock(data=_LEDGER_QUERY(self.sb, self._filters))

        if self.name == 'users' and self._action == 'select':
            uid = next((v for k, c, v in self._filters if c == 'id'), None)
            row = getattr(self.sb, 'users', {}).get(uid)
            return MagicMock(data=[row] if row else [])

        if self.name == 'maesil_insight_connections' and self._action == 'select':
            rows = getattr(self.sb, 'insight_rows', [])
            matched = list(rows)
            for kind, col, val in self._filters:
                if kind == 'eq':
                    matched = [r for r in matched if r.get(col) == val]
                elif kind == 'is' and val == 'null':
                    matched = [r for r in matched if not r.get(col)]
            return MagicMock(data=matched[:1] if matched else [])

        if self.name == 'brand_profiles' and self._action == 'select':
            rows = getattr(self.sb, 'brand_rows', [])
            matched = list(rows)
            for kind, col, val in self._filters:
                if kind == 'eq':
                    matched = [r for r in matched if r.get(col) == val]
                elif kind == 'is' and val == 'null':
                    matched = [r for r in matched if not r.get(col)]
            return MagicMock(data=matched)

        if self.name == 'user_brand_access' and self._action == 'select':
            return MagicMock(data=[])

        return MagicMock(data=[])


def _LEDGER_QUERY(sb: _FakeSupabase, filters):
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

from flask import Flask
test_app = Flask(__name__)
test_app.config['TESTING'] = True


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
    check('subscriptions.operator_id 추가', 'ALTER TABLE subscriptions' in sql3)
    check('payments.operator_id 추가', 'ALTER TABLE payments' in sql3)
    check('creations.operator_id 추가', 'ALTER TABLE creations' in sql3)
    check('FK ON DELETE CASCADE',
          'REFERENCES operators(id) ON DELETE CASCADE' in sql3)
    check('partial 인덱스 idx_point_ledger_operator',
          'idx_point_ledger_operator' in sql3 and 'WHERE operator_id IS NOT NULL' in sql3)
    check('Backfill UPDATE point_ledger',
          'UPDATE point_ledger pl' in sql3 and 'SET operator_id = u.operator_id' in sql3)
    check('Backfill UPDATE creations', 'UPDATE creations c' in sql3)
    check('초대 멤버 trial 정리',
          "u.site_role = 'user'" in sql3 and "s.status = 'trial'" in sql3
          and "status = 'cancelled'" in sql3)
    check('NOTIFY pgrst 스키마 리로드', "NOTIFY pgrst, 'reload schema'" in sql3)


print('\n[2b] migrations/004_brand_operator_pool.sql')
m4 = os.path.join(ROOT, 'migrations', '004_brand_operator_pool.sql')
check('파일 존재', os.path.isfile(m4))
if os.path.isfile(m4):
    sql4 = open(m4, encoding='utf-8').read()
    check('brand_profiles.operator_id 추가',
          'ALTER TABLE brand_profiles' in sql4
          and 'ADD COLUMN IF NOT EXISTS operator_id' in sql4)
    check('products.operator_id 추가',
          'ALTER TABLE products' in sql4
          and 'ADD COLUMN IF NOT EXISTS operator_id' in sql4)
    check('brand_profiles Backfill',
          'UPDATE brand_profiles bp' in sql4
          and 'SET operator_id = u.operator_id' in sql4)
    check('products Backfill',
          'UPDATE products p' in sql4
          and 'SET operator_id = u.operator_id' in sql4)
    check('idx_brand_profiles_operator 인덱스', 'idx_brand_profiles_operator' in sql4)
    check('idx_products_operator 인덱스', 'idx_products_operator' in sql4)
    check('ON DELETE SET NULL (브랜드/상품 — CASCADE 아님)',
          'ON DELETE SET NULL' in sql4)


print('\n[2c] migrations/005_insight_operator_pool.sql')
m5 = os.path.join(ROOT, 'migrations', '005_insight_operator_pool.sql')
check('파일 존재', os.path.isfile(m5))
if os.path.isfile(m5):
    sql5 = open(m5, encoding='utf-8').read()
    check('maesil_insight_connections.operator_id 추가',
          'ALTER TABLE maesil_insight_connections' in sql5
          and 'ADD COLUMN IF NOT EXISTS operator_id' in sql5)
    check('UNIQUE 인덱스 — operator 당 1개',
          'uq_insight_conn_operator' in sql5
          and 'WHERE operator_id IS NOT NULL' in sql5)


# ──────────────────────────────────────────────────────────
# 3. models.get_blog_cost — 분량 + variant 할인
# ──────────────────────────────────────────────────────────
print('\n[3] models.get_blog_cost')
from models import get_blog_cost, BLOG_LENGTH_COSTS, BLOG_ANGLE_OPTIONS, RELATION_MODE_OPTIONS

check('500자 = 20P',  get_blog_cost(500) == 20)
check('1000자 = 40P', get_blog_cost(1000) == 40)
check('2000자 = 80P', get_blog_cost(2000) == 80)
check('알 수 없는 분량 → 1000자 폴백 40P', get_blog_cost(777) == 40)
check('variant 모드 50% 할인 (1000자 → 20P)', get_blog_cost(1000, 'variant') == 20)
check('variant 모드 (2000자 → 40P)', get_blog_cost(2000, 'variant') == 40)
check('variant 모드 (500자 → 10P)', get_blog_cost(500, 'variant') == 10)
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
    check('general → 일반만', '100%' in general and '효능' not in general)

    unknown = get_system_avoid_words('unknown_category')
    check('알 수 없는 카테고리 → general 폴백',
          '100%' in unknown and '효능' not in unknown)

    brand = {'avoid_words': ['브랜드 금지1', '효능']}
    product = {'avoid_words': ['상품 금지1', '두뇌발달']}
    merged = combine_avoid_words(brand, product, 'baby_food')
    check('3-tier: 시스템+브랜드+상품 모두 포함',
          '효능' in merged and '브랜드 금지1' in merged
          and '상품 금지1' in merged and '두뇌발달' in merged and '100%' in merged)
    check('3-tier 중복 제거', merged.count('효능') == 1 and merged.count('두뇌발달') == 1)

    d_food = get_disclaimer('food')
    check('food 디스클레이머 = regulated 본문', '광고·표시 규정' in d_food)

    d_general = get_disclaimer('general')
    check('general 디스클레이머 = general 본문', '게시 전 검토' in d_general)

    d_unknown = get_disclaimer('cosmetics')
    check('cosmetics → regulated', '광고·표시 규정' in d_unknown)

    appended = append_disclaimer('블로그 본문 텍스트', 'food')
    check('append_disclaimer: 본문 + 구분선 + 디스클레이머',
          '블로그 본문 텍스트' in appended and '광고·표시 규정' in appended
          and '\n---\n' in appended)

    appended_twice = append_disclaimer(appended, 'food')
    check('append_disclaimer: 중복 부착 방지', appended_twice.count('광고·표시 규정') == 1)

    text = '이 제품은 면역력을 높이고 효능이 뛰어납니다. 100% 안전합니다.'
    found = scan_violations(text, ['효능', '면역력', '치료', '100%', 'NotInText'])
    check('scan_violations: 검출됨', '효능' in found and '면역력' in found and '100%' in found)
    check('scan_violations: 미존재 단어 제외', '치료' not in found and 'NotInText' not in found)
    check('scan_violations: 빈 입력 안전',
          scan_violations('', ['효능']) == [] and scan_violations('text', []) == [])


# ──────────────────────────────────────────────────────────
# 5. services.point_service._resolve_owner
# ──────────────────────────────────────────────────────────
print('\n[5] services.point_service — owner 분기')

with test_app.app_context():
    test_app.supabase = _FakeSupabase(CONFIG_SEED)
    test_app.supabase.users = {
        'u-personal': {'operator_id': None},
        'u-admin':    {'operator_id': 'op-1'},
        'u-member':   {'operator_id': 'op-1'},
    }

    from services.point_service import _resolve_owner

    class _U:
        def __init__(self, id, op=None):
            self.id = id
            self.operator_id = op
            self.is_authenticated = True

    uid, op = _resolve_owner(_U('u-personal', None))
    check('User 객체(개인) → user_id, operator_id=None', uid == 'u-personal' and op is None)

    uid, op = _resolve_owner(_U('u-admin', 'op-1'))
    check('User 객체(팀) → user_id + operator_id', uid == 'u-admin' and op == 'op-1')

    uid, op = _resolve_owner({'id': 'u-x', 'operator_id': 'op-2'})
    check('dict 입력 정상', uid == 'u-x' and op == 'op-2')

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
    sb.users = {
        'u-personal': {'operator_id': None},
        'u-admin':    {'operator_id': 'op-1'},
        'u-member':   {'operator_id': 'op-1'},
    }
    sb.ledger_rows = [
        {'user_id': 'u-personal', 'operator_id': None,
         'balance': 500,  'created_at': '2026-04-01T10:00:00'},
        {'user_id': 'u-admin',    'operator_id': 'op-1',
         'balance': 3000, 'created_at': '2026-04-15T10:00:00'},
        {'user_id': 'u-member',   'operator_id': 'op-1',
         'balance': 2960, 'created_at': '2026-04-20T10:00:00'},
    ]
    test_app.supabase = sb

    from services.point_service import get_balance

    class _U:
        def __init__(self, id, op=None):
            self.id, self.operator_id, self.is_authenticated = id, op, True

    check('개인 사용자 잔액 = user 풀 (500)', get_balance(_U('u-personal')) == 500)
    check('팀 admin 잔액 = operator 풀 최신 (2960)', get_balance(_U('u-admin', 'op-1')) == 2960)
    check('팀 member 잔액 = operator 풀 (2960)', get_balance(_U('u-member', 'op-1')) == 2960)
    check('다른 operator 잔액 = 0 (격리됨)', get_balance(_U('u-other', 'op-2')) == 0)


# ──────────────────────────────────────────────────────────
# 7. point_service.use_points / add_points — operator_id 행 기록
# ──────────────────────────────────────────────────────────
print('\n[7] point_service.use_points / add_points — operator_id 같이 기록')

with test_app.app_context():
    sb = _FakeSupabase(CONFIG_SEED)
    sb.users = {'u-admin': {'operator_id': 'op-1'}, 'u-member': {'operator_id': 'op-1'}}
    sb.ledger_rows = [
        {'user_id': 'u-admin', 'operator_id': 'op-1',
         'balance': 1000, 'created_at': '2026-04-15T10:00:00'},
    ]
    test_app.supabase = sb

    from services.point_service import use_points, add_points

    class _U:
        def __init__(self, id, op=None):
            self.id, self.operator_id, self.is_authenticated = id, op, True

    new_bal = use_points(_U('u-member', 'op-1'), 'blog', 'creation-1',
                         cost_override=80, note_override='블로그 (1,000자)')
    check('member 차감 후 잔액 = 920', new_bal == 920)

    inserted = sb.inserts[-1]
    check('insert 테이블 = point_ledger', inserted[0] == 'point_ledger')
    payload = inserted[1]
    check('차감 행에 operator_id 기록됨', payload.get('operator_id') == 'op-1')
    check('차감 행에 user_id (member) 도 기록됨 (감사용)', payload.get('user_id') == 'u-member')
    check('차감 amount 음수 (-80)', payload.get('amount') == -80)
    check('차감 type=use', payload.get('type') == 'use')
    check('차감 note=ledger_note', payload.get('note') == '블로그 (1,000자)')

    add_points(_U('u-admin', 'op-1'), 3000, 'subscription_grant',
               ref_id='pay-x', note='Starter 구독 포인트 지급')
    payload = sb.inserts[-1][1]
    check('충전 행에 operator_id', payload.get('operator_id') == 'op-1')
    check('충전 amount 양수 (+3000)', payload.get('amount') == 3000)


# ──────────────────────────────────────────────────────────
# 8. blueprints.billing — 권한 분기
# ──────────────────────────────────────────────────────────
print('\n[8] blueprints.billing')

import importlib
billing_mod = importlib.import_module('blueprints.billing')
check('billing.py 임포트', hasattr(billing_mod, 'billing_bp'))
check('_can_manage_subscription 정의됨',
      callable(getattr(billing_mod, '_can_manage_subscription', None)))

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
        check('개인 사용자 → 결제 가능', billing_mod._can_manage_subscription() is True)
    with patch('blueprints.billing.current_user', _MockUser('op-1', 'operator_admin')):
        check('operator_admin → 결제 가능', billing_mod._can_manage_subscription() is True)
    with patch('blueprints.billing.current_user', _MockUser('op-1', 'user')):
        check('일반 팀 멤버 → 결제 불가', billing_mod._can_manage_subscription() is False)


# ──────────────────────────────────────────────────────────
# 9. blueprints.main — _get_brand_count (OR logic) + history_detail
# ──────────────────────────────────────────────────────────
print('\n[9] blueprints.main — _get_brand_count / history_detail 스코핑')

main_mod = importlib.import_module('blueprints.main')
check('main.py 임포트', hasattr(main_mod, 'main_bp'))
check('_scoped_creations_query 정의됨',
      callable(getattr(main_mod, '_scoped_creations_query', None)))
check('_get_brand_count 정의됨 (_scoped_brands_query 대체)',
      callable(getattr(main_mod, '_get_brand_count', None)))
check('_scoped_brands_query 제거됨 (OR logic은 get_accessible_brands로 위임)',
      not callable(getattr(main_mod, '_scoped_brands_query', None)))

# _get_brand_count: operator 브랜드 + user 소유 브랜드(operator_id=NULL) 모두 카운트
with test_app.app_context():
    sb = _FakeSupabase(CONFIG_SEED)
    # operator admin 브랜드: 1개는 operator_id 있음, 1개는 없음 (구형)
    sb.brand_rows = [
        {'id': 'b-1', 'user_id': 'u-test', 'operator_id': 'op-1', 'is_default': True, 'created_at': '2026-01-01'},
        {'id': 'b-2', 'user_id': 'u-test', 'operator_id': None,   'is_default': False, 'created_at': '2026-01-02'},
    ]
    test_app.supabase = sb

    mu = _MockUser('op-1', 'operator_admin')
    with patch('blueprints.create._base.current_user', mu):
        count = main_mod._get_brand_count(sb)
    check('_get_brand_count: operator 브랜드(1) + 소유 구형 브랜드(1) = 2', count == 2)

    # 개인 사용자: user_id만 매칭
    sb2 = _FakeSupabase(CONFIG_SEED)
    sb2.brand_rows = [
        {'id': 'b-3', 'user_id': 'u-test', 'operator_id': None, 'is_default': True, 'created_at': '2026-01-01'},
    ]
    test_app.supabase = sb2
    mu_personal = _MockUser(None, 'user')
    with patch('blueprints.create._base.current_user', mu_personal):
        count_p = main_mod._get_brand_count(sb2)
    check('_get_brand_count: 개인 사용자 = 1', count_p == 1)

# history_detail 소스 코드 — operator 스코핑 확인
import inspect
detail_src = inspect.getsource(main_mod.history_detail)
check('history_detail: operator_id 분기 존재',
      'current_user.operator_id' in detail_src
      and 'operator_id' in detail_src)
check('history_detail: 개인 모드 user_id 체크 유지',
      'user_id' in detail_src and 'current_user.id' in detail_src)


# ──────────────────────────────────────────────────────────
# 10. services.maesil_insight_connection — operator 공유 연결
# ──────────────────────────────────────────────────────────
print('\n[10] services.maesil_insight_connection — operator 공유 연결')

with test_app.app_context():
    # 관리자가 등록한 operator 단위 연결
    sb = _FakeSupabase(CONFIG_SEED)
    sb.insight_rows = [
        {
            'user_id': 'u-admin', 'operator_id': 'op-1',
            'token_encrypted': 'ENC_TOKEN', 'token_prefix': 'mi_adminx',
            'insight_operator_name': '배마마', 'insight_plan': 'pro',
            'scopes': ['products:read'], 'connected_at': '2026-05-01T10:00:00',
        }
    ]
    test_app.supabase = sb

    from services.maesil_insight_connection import (
        get_connection, get_connection_token, save_connection,
        disconnect as conn_disconnect, mark_used, mark_error,
    )

    # 관리자 조회
    conn_admin = get_connection('u-admin', operator_id='op-1')
    check('관리자 → operator 연결 반환', conn_admin is not None and conn_admin.get('user_id') == 'u-admin')

    # 팀원 조회 → 관리자가 등록한 연결 공유
    conn_member = get_connection('u-member', operator_id='op-1')
    check('팀원 → 같은 operator 연결 공유 반환', conn_member is not None)
    check('팀원 조회 시 연결 운영사명 일치', conn_member.get('insight_operator_name') == '배마마')

    # 개인 사용자는 자신의 연결만
    conn_personal = get_connection('u-personal', operator_id=None)
    check('개인 사용자 → operator 연결 없음 (None)', conn_personal is None)

    # 다른 operator 는 격리
    conn_other = get_connection('u-other', operator_id='op-2')
    check('다른 operator → None (격리)', conn_other is None)


print('\n[10b] maesil_insight_connection — save/disconnect operator 모드')

with test_app.app_context():
    sb = _FakeSupabase(CONFIG_SEED)
    sb.insight_rows = []  # 기존 연결 없음
    test_app.supabase = sb

    # operator 모드 save → insert 호출
    row = save_connection(
        'u-admin',
        token='mi_testtoken123',
        me={'operator_name': '배마마', 'plan': 'pro', 'scopes': ['products:read']},
        operator_id='op-1',
    )
    check('save_connection(operator): insert 호출됨', len(sb.inserts) == 1)
    check('save_connection(operator): 테이블=maesil_insight_connections',
          sb.inserts[0][0] == 'maesil_insight_connections')
    payload = sb.inserts[0][1]
    check('save_connection(operator): operator_id 채워짐',
          payload.get('operator_id') == 'op-1')
    check('save_connection(operator): user_id 채워짐',
          payload.get('user_id') == 'u-admin')
    check('save_connection(operator): token_prefix 저장됨',
          payload.get('token_prefix') == 'mi_testtoke')

    # 개인 모드 save → upsert(on_conflict=user_id)
    sb2 = _FakeSupabase(CONFIG_SEED)
    sb2.insight_rows = []
    test_app.supabase = sb2
    save_connection(
        'u-personal',
        token='mi_perstoken1',
        me={'operator_name': '', 'plan': 'free', 'scopes': []},
        operator_id=None,
    )
    check('save_connection(개인): upsert 호출됨', len(sb2.upserts) == 1)
    check('save_connection(개인): on_conflict=user_id',
          sb2.upserts[0][2] == 'user_id')
    check('save_connection(개인): operator_id 없음',
          'operator_id' not in sb2.upserts[0][1])

    # disconnect operator 모드
    sb3 = _FakeSupabase(CONFIG_SEED)
    test_app.supabase = sb3
    conn_disconnect('u-admin', operator_id='op-1')
    check('disconnect(operator): delete 호출됨', len(sb3.deletes) == 1)
    del_filters = sb3.deletes[0][1]
    check('disconnect(operator): operator_id 기준 삭제',
          any(c == 'operator_id' and v == 'op-1' for _, c, v in del_filters))

    # disconnect 개인 모드
    sb4 = _FakeSupabase(CONFIG_SEED)
    test_app.supabase = sb4
    conn_disconnect('u-personal', operator_id=None)
    del_filters4 = sb4.deletes[0][1]
    check('disconnect(개인): user_id 기준 삭제',
          any(c == 'user_id' and v == 'u-personal' for _, c, v in del_filters4))

    # mark_used operator 모드
    sb5 = _FakeSupabase(CONFIG_SEED)
    test_app.supabase = sb5
    mark_used('u-admin', operator_id='op-1')
    check('mark_used(operator): update 호출됨', len(sb5.updates) == 1)
    check('mark_used(operator): last_used_at 업데이트',
          'last_used_at' in sb5.updates[0][1])
    # update 후 eq 필터 확인
    upd_table = sb5.updates[0][0]
    check('mark_used: 테이블=maesil_insight_connections',
          upd_table == 'maesil_insight_connections')


# ──────────────────────────────────────────────────────────
# 11. blueprints.integrations — _can_manage / _op_id
# ──────────────────────────────────────────────────────────
print('\n[11] blueprints.integrations — 권한 분기')

integ_mod = importlib.import_module('blueprints.integrations')
check('integrations.py 임포트', hasattr(integ_mod, 'integrations_bp'))
check('_can_manage 정의됨', callable(getattr(integ_mod, '_can_manage', None)))
check('_op_id 정의됨', callable(getattr(integ_mod, '_op_id', None)))

with test_app.app_context():
    with patch('blueprints.integrations.current_user', _MockUser(None, 'user')):
        check('개인 사용자 → _can_manage True', integ_mod._can_manage() is True)
        check('개인 사용자 → _op_id None', integ_mod._op_id() is None)

    with patch('blueprints.integrations.current_user', _MockUser('op-1', 'operator_admin')):
        check('operator_admin → _can_manage True', integ_mod._can_manage() is True)
        check('operator_admin → _op_id 반환', integ_mod._op_id() == 'op-1')

    with patch('blueprints.integrations.current_user', _MockUser('op-1', 'user')):
        check('일반 팀원 → _can_manage False', integ_mod._can_manage() is False)
        check('일반 팀원 → _op_id 반환됨', integ_mod._op_id() == 'op-1')

# 소스 코드에서 operator_id 전달 확인
import inspect
idx_src = inspect.getsource(integ_mod.index)
connect_src = inspect.getsource(integ_mod.connect)
disconnect_src = inspect.getsource(integ_mod.disconnect)

check('index: get_connection에 operator_id 전달',
      'operator_id=_op_id()' in idx_src)
check('connect: can_manage 체크 존재', '_can_manage()' in connect_src)
check('connect: verify_and_save에 operator_id 전달',
      'operator_id=op_id' in connect_src)
check('disconnect: can_manage 체크 존재', '_can_manage()' in disconnect_src)
check('disconnect: conn_disconnect에 operator_id 전달',
      'operator_id=op_id' in disconnect_src)


# ──────────────────────────────────────────────────────────
# 12. templates/history/index.html — 그룹 뷰 마크업
# ──────────────────────────────────────────────────────────
print('\n[12] templates/history/index.html')

tpl = os.path.join(ROOT, 'templates', 'history', 'index.html')
check('파일 존재', os.path.isfile(tpl))
if os.path.isfile(tpl):
    html = open(tpl, encoding='utf-8').read()
    check('TYPE_META 정의 (유형별 컬러/아이콘)', 'TYPE_META' in html)
    check('blog 메타 정의', "'blog'" in html and "bi-file-text" in html)
    check('instagram 메타 정의', "'instagram'" in html and 'bi-instagram' in html)
    check('그룹 뷰 분기 (view_mode == grouped)', "view_mode == 'grouped'" in html)
    check('평면 뷰 토글 링크 (?view=flat)', '?view=flat' in html)
    check('유형별 카운트 뱃지', '{{ g.count }}' in html)
    check('전체 보기 링크 (g.has_more)', 'g.has_more' in html)
    check('팀 모드 배너', 'is_team_mode' in html and '팀 공유' in html)


# ──────────────────────────────────────────────────────────
# 13. templates/integrations/index.html — 팀원 뷰 분기
# ──────────────────────────────────────────────────────────
print('\n[13] templates/integrations/index.html — 팀원 뷰 분기')

integ_tpl = os.path.join(ROOT, 'templates', 'integrations', 'index.html')
check('파일 존재', os.path.isfile(integ_tpl))
if os.path.isfile(integ_tpl):
    itpl = open(integ_tpl, encoding='utf-8').read()
    check('can_manage 분기 존재', 'can_manage' in itpl)
    check('팀원 안내 메시지', '팀 관리자' in itpl)
    check('연결 해제 버튼 can_manage 조건부', 'can_manage' in itpl and '연결 해제' in itpl)
    check('팀원 미연결 안내 별도 표시', '팀 관리자에게 연결 설정을 요청' in itpl)


# ──────────────────────────────────────────────────────────
# 14. auth.py — 초대 멤버 trial 미생성
# ──────────────────────────────────────────────────────────
print('\n[14] auth.py — 초대 멤버 trial 분기')

auth_path = os.path.join(ROOT, 'auth.py')
auth_src = open(auth_path, encoding='utf-8').read()
check('is_invited_member 분기 존재', 'is_invited_member' in auth_src)
check("biz_sub == 'invited' 매칭", "biz_sub == 'invited'" in auth_src)
check('초대 멤버 → subscription insert 스킵', 'if not is_invited_member' in auth_src)
check('신규 operator 관리자 trial 에 operator_id 매핑',
      "sub_row['operator_id'] = new_op_id" in auth_src)


# ──────────────────────────────────────────────────────────
# 15. blueprints.create.blog — 4축 + 관계 모드
# ──────────────────────────────────────────────────────────
print('\n[15] blueprints.create.blog — 4축 + 관계 모드')

blog_mod = importlib.import_module('blueprints.create.blog')
check('blog.py 임포트', hasattr(blog_mod, 'blog'))
check('blog_generate 라우트 정의', hasattr(blog_mod, 'blog_generate'))
check('blog_products (브랜드 변경 AJAX)', hasattr(blog_mod, 'blog_products'))
check('_detect_category 정의', callable(getattr(blog_mod, '_detect_category', None)))

norm = blog_mod._normalize_category
check('카테고리 정규화 — 이유식', norm('이유식') == 'baby_food')
check('카테고리 정규화 — 화장품', norm('화장품') == 'cosmetics')
check('카테고리 정규화 — 빈 입력', norm('') == '')

detect = blog_mod._detect_category
check('상품 카테고리 우선',
      detect({'industry': '식품'}, {'category': '이유식'}) == 'baby_food')
check('상품 없으면 브랜드 업종',
      detect({'industry': '화장품'}, None) == 'cosmetics')
check('둘 다 없으면 general', detect({}, None) == 'general')


# ──────────────────────────────────────────────────────────
# 16. services.prompts.blog — 프롬프트 빌드
# ──────────────────────────────────────────────────────────
print('\n[16] services.prompts.blog — 프롬프트 빌드')

with test_app.app_context():
    test_app.supabase = _FakeSupabase(CONFIG_SEED)

    from services.prompts.blog import build_prompt

    brand = {'name': '배마마', 'industry': '이유식', 'target_customer': '30대 엄마'}
    product = {'name': '야채큐브', 'category': '이유식', 'price': 18900,
               'features': ['첨가물 무첨가', '월령별']}

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
    check('system 프롬프트에 금지 표현 강조',
          '절대 사용 금지' in sys_p and '효능' in sys_p)
    check('user 프롬프트 — 이력 회피 모드', '다양성 모드' in user_p)

    sys_s, user_s, t2 = build_prompt(
        brand,
        {'topic': '이유식재료', 'keyword': '야채큐브', 'angle': 'timeline',
         'length': '2000', 'relation_mode': 'series'},
        related_creation={'title': '시리즈 1편 — 이유식 입문',
                          'excerpt': '이전 글 본문 발췌입니다.'},
    )
    check('series 모드 max_tokens=6000', t2 == 6000)
    check('series 모드 — 후속편 지시 포함', '시리즈 후속편' in user_s)


# ──────────────────────────────────────────────────────────
# 17. services.claude_service.build_brand_context
# ──────────────────────────────────────────────────────────
print('\n[17] services.claude_service.build_brand_context')

from services.claude_service import build_brand_context

ctx_simple = build_brand_context({'name': '배마마'})
check('단일 브랜드 컨텍스트', '배마마' in ctx_simple)

ctx_with_product = build_brand_context(
    {'name': '배마마'},
    product={'name': '야채큐브', 'price': 18900, 'features': ['무첨가', '월령별']},
)
check('상품 컨텍스트 — 상품명', '야채큐브' in ctx_with_product)
check('상품 컨텍스트 — 가격 포맷', '18,900원' in ctx_with_product)
check('상품 컨텍스트 — 특징', '무첨가' in ctx_with_product)

ctx_avoids = build_brand_context(
    {'name': '배마마'},
    merged_avoid_words=['효능', '치료'],
)
check('merged_avoid_words 주입 — 강조 문구', '절대 사용 금지' in ctx_avoids and '효능' in ctx_avoids)


# ──────────────────────────────────────────────────────────
# 18. _base.py run_text_generation 시그니처
# ──────────────────────────────────────────────────────────
print('\n[18] blueprints/create/_base.py')

base_mod = importlib.import_module('blueprints.create._base')
import inspect
sig = inspect.signature(base_mod.run_text_generation)
params = sig.parameters
check('point_cost 파라미터', 'point_cost' in params)
check('ledger_note 파라미터', 'ledger_note' in params)
check('extra_creation_fields 파라미터', 'extra_creation_fields' in params)
check('post_process 파라미터', 'post_process' in params)
check('max_tokens 파라미터', 'max_tokens' in params)


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
