"""Draft / Resume 기능 검증 하네스.

검증 대상:
  - migrations/006_draft_resume.sql — step_reached / step_data / updated_at 컬럼
  - blueprints/create/blog.py      — _get_blog_drafts, blog_save_draft, blog_get_draft, blog_delete_draft
  - blueprints/create/instagram.py — _get_instagram_drafts, instagram_save_draft, instagram_get_draft, instagram_delete_draft
  - blueprint 라우트 등록 (6개 엔드포인트)
  - templates/create/blog.html     — 배너 마크업, JS 함수 4개, 자동저장 호출 2곳
  - templates/create/instagram.html — 배너 마크업, JS 함수 4개, 자동저장 호출 2곳

Supabase 미연결 환경에서도 통과 (인메모리 mock + 순수 함수 단위).
실행: py -3 -X utf8 test_draft_resume_harness.py
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import sys
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = '\033[92m PASS\033[0m'
FAIL = '\033[91m FAIL\033[0m'
results: list[tuple[str, bool]] = []


def check(label: str, condition, detail: str = ''):
    mark = PASS if condition else FAIL
    print(f'{mark}  {label}' + (f'  [{detail}]' if detail else ''))
    results.append((label, bool(condition)))


# ── 환경 ──────────────────────────────────────────────────────
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-key')
os.environ.setdefault('SUPABASE_URL', 'https://placeholder.supabase.co')
os.environ.setdefault('SUPABASE_SERVICE_KEY', 'placeholder')
os.environ.setdefault('ENCRYPTION_KEY', 'a' * 32)


# ── 공통 Mock Supabase ─────────────────────────────────────────
class _FakeTable:
    """Supabase 체이닝 mock — execute()에서 실제 데이터 반환."""

    def __init__(self, db: '_FakeDB', name: str):
        self._db   = db
        self._name = name
        self._action   = None
        self._payload  = None
        self._filters: list[tuple[str, str, object]] = []
        self._select_cols = '*'

    # ── 빌더 ──
    def select(self, cols='*', **_kw):
        self._select_cols = cols
        self._action = 'select'
        return self

    def insert(self, payload):
        self._action  = 'insert'
        self._payload = payload
        self._db.inserts.append((self._name, payload))
        return self

    def update(self, payload):
        self._action  = 'update'
        self._payload = payload
        self._db.updates.append((self._name, payload))
        return self

    def upsert(self, payload, on_conflict=None):
        self._action  = 'upsert'
        self._payload = payload
        self._db.upserts.append((self._name, payload, on_conflict))
        return self

    def delete(self):
        self._action = 'delete'
        return self

    def eq(self, col, val):
        self._filters.append(('eq', col, val))
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, n):
        return self

    def execute(self):
        name = self._name

        # DELETE
        if self._action == 'delete':
            self._db.deletes.append((name, list(self._filters)))
            return MagicMock(data=[])

        # SELECT — creations 테이블
        if name == 'creations' and self._action == 'select':
            rows = list(self._db.rows.get('creations', []))
            for kind, col, val in self._filters:
                if kind == 'eq':
                    rows = [r for r in rows if r.get(col) == val]
            return MagicMock(data=rows)

        # INSERT / UPDATE
        if self._action in ('insert', 'update', 'upsert'):
            return MagicMock(data=[self._payload])

        return MagicMock(data=[])


class _FakeDB:
    def __init__(self, rows: dict | None = None):
        self.rows    = rows or {}
        self.inserts: list = []
        self.updates: list = []
        self.deletes: list = []
        self.upserts: list = []

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self, name)


# ──────────────────────────────────────────────────────────────
# 0. 마이그레이션 파일 검사
# ──────────────────────────────────────────────────────────────
print('\n[0] migrations/006_draft_resume.sql')

mig_path = os.path.join(ROOT, 'migrations', '008_draft_resume.sql')
mig_exists = os.path.isfile(mig_path)
check('파일 존재', mig_exists)

if mig_exists:
    sql = open(mig_path, encoding='utf-8').read()
    check('step_reached 컬럼 추가',
          'step_reached' in sql and 'ADD COLUMN IF NOT EXISTS' in sql)
    check('step_data JSONB 컬럼 추가',
          'step_data' in sql and ('jsonb' in sql.lower() or 'JSONB' in sql))
    check('updated_at timestamptz 컬럼 추가',
          'updated_at' in sql and ('timestamptz' in sql.lower() or 'TIMESTAMPTZ' in sql))
    check("status 'draft' 허용 (CHECK 또는 주석)",
          'draft' in sql)
    check('멱등성 보장 — IF NOT EXISTS',
          sql.count('IF NOT EXISTS') >= 3)
else:
    for label in ['step_reached 컬럼 추가', 'step_data JSONB 컬럼 추가',
                  'updated_at timestamptz 컬럼 추가',
                  "status 'draft' 허용 (CHECK 또는 주석)",
                  '멱등성 보장 — IF NOT EXISTS']:
        check(label, False, 'migration 파일 없음')


# ──────────────────────────────────────────────────────────────
# 1. blog.py — 임포트 & 라우트 등록
# ──────────────────────────────────────────────────────────────
print('\n[1] blueprints.create.blog — 라우트 등록')

from flask import Flask
test_app = Flask(__name__)
test_app.config.update(TESTING=True, SECRET_KEY='test', WTF_CSRF_ENABLED=False)

try:
    from blueprints.create import create_bp
    # app.py 와 동일하게 url_prefix='/create' 로 등록
    _route_app = Flask(__name__ + '_routes')
    _route_app.config.update(TESTING=True, SECRET_KEY='test', WTF_CSRF_ENABLED=False)
    _route_app.register_blueprint(create_bp, url_prefix='/create')

    rules = {(str(r.rule), frozenset(r.methods - {'HEAD', 'OPTIONS'}))
             for r in _route_app.url_map.iter_rules()}
    rule_urls = {r[0] for r in rules}

    def _has_route(url, methods):
        want = frozenset(methods)
        return any(u == url and want <= ms for u, ms in rules)

    check('POST /create/blog/save-draft 등록',
          _has_route('/create/blog/save-draft', {'POST'}))
    check('GET  /create/blog/draft/<id> 등록',
          any(u.startswith('/create/blog/draft/') for u in rule_urls))
    check('DELETE /create/blog/draft/<id> 등록',
          any(u.startswith('/create/blog/draft/') for u in rule_urls))
    check('POST /create/instagram/save-draft 등록',
          _has_route('/create/instagram/save-draft', {'POST'}))
    check('GET  /create/instagram/draft/<id> 등록',
          any(u.startswith('/create/instagram/draft/') for u in rule_urls))
    check('DELETE /create/instagram/draft/<id> 등록',
          any(u.startswith('/create/instagram/draft/') for u in rule_urls))

    # blog draft/<id> 가 GET 과 DELETE 를 모두 지원하는지
    blog_draft_methods: set[str] = set()
    for url, ms in rules:
        if url.startswith('/create/blog/draft/'):
            blog_draft_methods |= set(ms)
    check('blog/draft/<id> — GET+DELETE 모두 지원',
          'GET' in blog_draft_methods and 'DELETE' in blog_draft_methods,
          str(sorted(blog_draft_methods)))

    insta_draft_methods: set[str] = set()
    for url, ms in rules:
        if url.startswith('/create/instagram/draft/'):
            insta_draft_methods |= set(ms)
    check('instagram/draft/<id> — GET+DELETE 모두 지원',
          'GET' in insta_draft_methods and 'DELETE' in insta_draft_methods,
          str(sorted(insta_draft_methods)))

except Exception as e:
    check('blueprint 등록 및 라우트 검사', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 2. blog.py — _get_blog_drafts 헬퍼 로직
# ──────────────────────────────────────────────────────────────
print('\n[2] blog.py — _get_blog_drafts 헬퍼')

try:
    blog_mod = importlib.import_module('blueprints.create.blog')
    _get_blog_drafts = blog_mod._get_blog_drafts

    _BLOG_ROWS = [
        {
            'id': 'draft-blog-1',
            'user_id': 'u-test',
            'operator_id': None,
            'creation_type': 'blog',
            'status': 'draft',
            'step_reached': 2,
            'input_data': {'direction': '신제품 소개 A' * 5},   # 50자 초과 → truncate
            'step_data': {},
            'output_data': {},
            'created_at': '2026-05-10T09:00:00',
            'updated_at': '2026-05-14T10:00:00',
        },
        {
            'id': 'draft-blog-2',
            'user_id': 'u-test',
            'operator_id': None,
            'creation_type': 'blog',
            'status': 'draft',
            'step_reached': 3,
            'input_data': {'direction': '단기 방향'},
            'step_data': {},
            'output_data': {},
            'created_at': '2026-05-12T08:00:00',
            'updated_at': '2026-05-13T11:00:00',
        },
    ]

    db = _FakeDB({'creations': _BLOG_ROWS})

    _mock_user = MagicMock()
    _mock_user.id = 'u-test'
    _mock_user.operator_id = None

    with test_app.app_context():
        with patch('blueprints.create.blog.current_user', _mock_user):
            drafts = _get_blog_drafts(db, limit=5)

    check('draft 2개 반환', len(drafts) == 2)
    check("step_reached=2 → step_label='소구포인트 선택'",
          any(d['step_label'] == '소구포인트 선택' and d['id'] == 'draft-blog-1' for d in drafts))
    check("step_reached=3 → step_label='글 초안 완료'",
          any(d['step_label'] == '글 초안 완료' for d in drafts))
    check('direction 50자 이하로 truncate',
          all(len(d['direction']) <= 50 for d in drafts))
    check('date = updated_at 앞 10자',
          any(d['date'] == '2026-05-14' for d in drafts))

    # status='done' 인 행은 제외됨 (draft 전용)
    _MIXED_ROWS = _BLOG_ROWS + [{
        'id': 'done-blog-1', 'user_id': 'u-test', 'operator_id': None,
        'creation_type': 'blog', 'status': 'done',
        'step_reached': 5, 'input_data': {}, 'step_data': {}, 'output_data': {},
        'created_at': '2026-05-01T00:00:00', 'updated_at': '2026-05-01T00:00:00',
    }]
    db2 = _FakeDB({'creations': _MIXED_ROWS})
    with test_app.app_context():
        with patch('blueprints.create.blog.current_user', _mock_user):
            drafts2 = _get_blog_drafts(db2, limit=10)
    check("status='done' 행은 draft 목록에서 제외",
          all(d['id'] != 'done-blog-1' for d in drafts2))

    # operator_id 있는 경우 → operator 기준으로 필터
    _mock_op = MagicMock()
    _mock_op.id = 'u-admin'
    _mock_op.operator_id = 'op-1'

    _OP_ROWS = [
        {
            'id': 'draft-op-1', 'user_id': 'u-admin', 'operator_id': 'op-1',
            'creation_type': 'blog', 'status': 'draft',
            'step_reached': 2, 'input_data': {'direction': '운영사 글'},
            'step_data': {}, 'output_data': {},
            'created_at': '2026-05-15T10:00:00', 'updated_at': '2026-05-15T10:00:00',
        },
    ]
    db3 = _FakeDB({'creations': _OP_ROWS + _BLOG_ROWS})
    with test_app.app_context():
        with patch('blueprints.create.blog.current_user', _mock_op):
            drafts3 = _get_blog_drafts(db3)
    check('operator_id 있는 유저 — operator 기준 쿼리 실행 (예외 없음)',
          isinstance(drafts3, list))

except Exception as e:
    check('_get_blog_drafts 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 3. instagram.py — _get_instagram_drafts 헬퍼 로직
# ──────────────────────────────────────────────────────────────
print('\n[3] instagram.py — _get_instagram_drafts 헬퍼')

try:
    insta_mod = importlib.import_module('blueprints.create.instagram')
    _get_instagram_drafts = insta_mod._get_instagram_drafts

    _INSTA_ROWS = [
        {
            'id': 'draft-insta-1',
            'user_id': 'u-test',
            'operator_id': None,
            'creation_type': 'instagram',
            'status': 'draft',
            'step_reached': 3,
            'input_data': {'direction': '봄 신상 홍보'},
            'step_data': {},
            'output_data': {},
            'created_at': '2026-05-13T09:00:00',
            'updated_at': '2026-05-14T15:00:00',
        },
    ]
    db_i = _FakeDB({'creations': _INSTA_ROWS})

    with test_app.app_context():
        with patch('blueprints.create.instagram.current_user', _mock_user):
            insta_drafts = _get_instagram_drafts(db_i, limit=5)

    check('insta draft 1개 반환', len(insta_drafts) == 1)
    check("step_reached=3 → step_label='캡션 완료'",
          insta_drafts[0]['step_label'] == '캡션 완료')
    check("direction 정상 추출",
          insta_drafts[0]['direction'] == '봄 신상 홍보')
    check("date = updated_at 앞 10자",
          insta_drafts[0]['date'] == '2026-05-14')

    # 단계 라벨 전체 점검
    _STEP_LABELS_INSTA = {
        1: '기본설정', 2: '소구포인트 선택', 3: '캡션 완료',
        4: '이미지 작업 중', 5: '완성',
    }
    for step, label in _STEP_LABELS_INSTA.items():
        row = {
            'id': f'test-{step}', 'user_id': 'u-test', 'operator_id': None,
            'creation_type': 'instagram', 'status': 'draft',
            'step_reached': step, 'input_data': {}, 'step_data': {}, 'output_data': {},
            'created_at': '2026-05-01T00:00:00', 'updated_at': '2026-05-01T00:00:00',
        }
        db_s = _FakeDB({'creations': [row]})
        with test_app.app_context():
            with patch('blueprints.create.instagram.current_user', _mock_user):
                res = _get_instagram_drafts(db_s)
        check(f"insta step_reached={step} → '{label}'",
              res and res[0]['step_label'] == label)

except Exception as e:
    check('_get_instagram_drafts 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 공통 헬퍼: @login_required bypass + current_user 주입
# ──────────────────────────────────────────────────────────────
def _passthrough(f):
    """@login_required 를 no-op 데코레이터로 교체."""
    return f

def _unwrap(fn):
    """@login_required 및 functools.wraps 체인을 벗겨낸 원본 함수 반환."""
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _call_view(app, view_fn, path, method='GET', json_body=None, patches=None,
               supabase=None):
    """@login_required bypass + current_user mock 하에서 뷰 함수 호출.

    Args:
        supabase: FakeDB 인스턴스. app.supabase 에 직접 바인딩.
        patches:  {target: obj} — current_user 등 모듈 수준 이름 패치.
        view_fn:  callable (lambda 포함). __wrapped__ 가 있으면 자동 unwrap.
    """
    # view_fn 이 lambda 일 경우 __wrapped__ 가 없으므로 내부에서 처리
    actual_fn = view_fn if callable(view_fn) and not hasattr(view_fn, '__wrapped__') \
                else _unwrap(view_fn)

    ctx_patches = patches or {}
    with app.app_context():
        if supabase is not None:
            app.supabase = supabase
        patchers = [patch(t, v) for t, v in ctx_patches.items()]
        for p in patchers:
            p.start()
        try:
            with app.test_request_context(
                path, method=method,
                json=json_body,
                content_type='application/json' if json_body is not None else None,
            ):
                # lambda: fn(arg) 형태로 전달될 경우 actual_fn == view_fn
                result = actual_fn()
                return json.loads(result.get_data(as_text=True))
        finally:
            for p in patchers:
                p.stop()


# ──────────────────────────────────────────────────────────────
# 4. blog_save_draft — INSERT(신규) / UPDATE(기존)
# ──────────────────────────────────────────────────────────────
print('\n[4] blog_save_draft — INSERT / UPDATE 분기')

try:
    _mock_user.id = 'u-test'

    _SAVE_PAYLOAD = {
        'draft_id':     None,
        'step_reached': 2,
        'input_data':   {'brand_id': 'b-1', 'direction': '테스트 방향', 'length': '1000',
                         'relation_mode': 'new'},
        'step_data':    {'angles': [{'id': 'A', 'title': '소구 A'}], 'selected_angle': None},
        'output_data':  {},
    }

    db_s1 = _FakeDB({'creations': []})
    data = _call_view(test_app, _unwrap(blog_mod.blog_save_draft),
                      '/create/blog/save-draft', method='POST', json_body=_SAVE_PAYLOAD,
                      patches={'blueprints.create.blog.current_user': _mock_user},
                      supabase=db_s1)

    check('save_draft NEW → ok=True', data.get('ok') is True)
    check('save_draft NEW → draft_id 반환됨',
          bool(data.get('draft_id')))
    check('save_draft NEW → INSERT 1건 발생', len(db_s1.inserts) == 1)

    ins_payload = db_s1.inserts[0][1]
    check("INSERT: creation_type='blog'", ins_payload.get('creation_type') == 'blog')
    check("INSERT: status='draft'",       ins_payload.get('status') == 'draft')
    check("INSERT: step_reached=2",       ins_payload.get('step_reached') == 2)
    check("INSERT: user_id 기록",         ins_payload.get('user_id') == 'u-test')
    check("INSERT: points_used=0",        ins_payload.get('points_used') == 0)
    check("INSERT: updated_at 포함",      bool(ins_payload.get('updated_at')))
    check("INSERT: id(UUID) 포함",        bool(ins_payload.get('id')))

    # UPDATE 분기 — draft_id 존재
    existing_id = 'existing-draft-uuid'
    _SAVE_PAYLOAD_UPD = {**_SAVE_PAYLOAD, 'draft_id': existing_id, 'step_reached': 3}
    db_s2 = _FakeDB({'creations': [
        {'id': existing_id, 'user_id': 'u-test', 'creation_type': 'blog',
         'status': 'draft', 'step_reached': 2,
         'input_data': {}, 'step_data': {}, 'output_data': {},
         'created_at': '2026-05-10T00:00:00', 'updated_at': '2026-05-10T00:00:00'},
    ]})

    data2 = _call_view(test_app, _unwrap(blog_mod.blog_save_draft),
                       '/create/blog/save-draft', method='POST', json_body=_SAVE_PAYLOAD_UPD,
                       patches={'blueprints.create.blog.current_user': _mock_user},
                       supabase=db_s2)

    check('save_draft UPDATE → ok=True', data2.get('ok') is True)
    check('save_draft UPDATE → 같은 draft_id 반환',
          data2.get('draft_id') == existing_id)
    check('save_draft UPDATE → UPDATE 1건 발생 (INSERT 없음)',
          len(db_s2.inserts) == 0 and len(db_s2.updates) == 1)
    check('save_draft UPDATE: step_reached=3',
          db_s2.updates[0][1].get('step_reached') == 3)

except Exception as e:
    check('blog_save_draft 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 5. instagram_save_draft — INSERT / UPDATE
# ──────────────────────────────────────────────────────────────
print('\n[5] instagram_save_draft — INSERT / UPDATE 분기')

try:
    _INSTA_SAVE_PAYLOAD = {
        'draft_id':     None,
        'step_reached': 2,
        'input_data':   {'brand_id': 'b-1', 'direction': '인스타 방향', 'relation_mode': 'new'},
        'step_data':    {'angles': [], 'selected_angle': None, 'caption_short': ''},
        'output_data':  {},
    }

    db_i1 = _FakeDB({'creations': []})
    data_i = _call_view(test_app, _unwrap(insta_mod.instagram_save_draft),
                        '/create/instagram/save-draft', method='POST',
                        json_body=_INSTA_SAVE_PAYLOAD,
                        patches={'blueprints.create.instagram.current_user': _mock_user},
                        supabase=db_i1)

    check('insta save_draft NEW → ok=True', data_i.get('ok') is True)
    check('insta save_draft NEW → draft_id 반환', bool(data_i.get('draft_id')))
    check('insta INSERT: creation_type=instagram',
          db_i1.inserts[0][1].get('creation_type') == 'instagram')
    check('insta INSERT: status=draft',
          db_i1.inserts[0][1].get('status') == 'draft')

    # UPDATE
    _INSTA_UPD_ID = 'insta-draft-existing'
    _INSTA_SAVE_UPD = {**_INSTA_SAVE_PAYLOAD, 'draft_id': _INSTA_UPD_ID, 'step_reached': 3,
                       'output_data': {'caption_short': '짧은 캡션', 'hashtag_text': '#테스트'}}
    db_i2 = _FakeDB({'creations': [
        {'id': _INSTA_UPD_ID, 'user_id': 'u-test', 'creation_type': 'instagram',
         'status': 'draft', 'step_reached': 2,
         'input_data': {}, 'step_data': {}, 'output_data': {},
         'created_at': '2026-05-13T00:00:00', 'updated_at': '2026-05-13T00:00:00'},
    ]})

    data_i2 = _call_view(test_app, _unwrap(insta_mod.instagram_save_draft),
                         '/create/instagram/save-draft', method='POST',
                         json_body=_INSTA_SAVE_UPD,
                         patches={'blueprints.create.instagram.current_user': _mock_user},
                         supabase=db_i2)

    check('insta save_draft UPDATE → ok=True', data_i2.get('ok') is True)
    check('insta save_draft UPDATE → 같은 id 반환',
          data_i2.get('draft_id') == _INSTA_UPD_ID)
    check('insta UPDATE: step_reached=3',
          db_i2.updates[0][1].get('step_reached') == 3)

except Exception as e:
    check('instagram_save_draft 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 6. blog_get_draft — status 접근 제어
# ──────────────────────────────────────────────────────────────
print('\n[6] blog_get_draft — 접근 제어 (draft/done ✓ / generating ✗)')

try:
    _ALL_ROWS = [
        {'id': 'row-draft',      'user_id': 'u-test', 'creation_type': 'blog',
         'status': 'draft',      'step_reached': 2, 'input_data': {'direction': '방향'},
         'step_data': {}, 'output_data': {}},
        {'id': 'row-done',       'user_id': 'u-test', 'creation_type': 'blog',
         'status': 'done',       'step_reached': 5, 'input_data': {},
         'step_data': {}, 'output_data': {'text': '완성본'}},
        {'id': 'row-generating', 'user_id': 'u-test', 'creation_type': 'blog',
         'status': 'generating', 'step_reached': 3, 'input_data': {},
         'step_data': {}, 'output_data': {}},
    ]
    db_g = _FakeDB({'creations': _ALL_ROWS})

    _blog_get_draft_fn = _unwrap(blog_mod.blog_get_draft)
    def _get(row_id):
        return _call_view(test_app,
                          lambda: _blog_get_draft_fn(row_id),
                          f'/create/blog/draft/{row_id}',
                          patches={'blueprints.create.blog.current_user': _mock_user},
                          supabase=db_g)

    r_draft = _get('row-draft')
    check("get_draft(draft)  → ok=True",  r_draft.get('ok') is True)
    check("get_draft(draft)  → draft 포함", 'draft' in r_draft)

    r_done  = _get('row-done')
    check("get_draft(done)   → ok=True",  r_done.get('ok') is True)

    r_gen   = _get('row-generating')
    check("get_draft(generating) → ok=False (접근 거부)",
          r_gen.get('ok') is False)

    r_miss  = _get('nonexistent')
    check("get_draft(없는 ID) → ok=False",
          r_miss.get('ok') is False)

except Exception as e:
    check('blog_get_draft 접근 제어', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 7. blog_delete_draft — status=draft 만 삭제
# ──────────────────────────────────────────────────────────────
print('\n[7] blog_delete_draft — status=draft 한정 삭제')

try:
    db_d = _FakeDB({'creations': _ALL_ROWS})

    _blog_delete_draft_fn = _unwrap(blog_mod.blog_delete_draft)
    def _del(row_id):
        return _call_view(test_app,
                          lambda: _blog_delete_draft_fn(row_id),
                          f'/create/blog/draft/{row_id}', method='DELETE',
                          patches={'blueprints.create.blog.current_user': _mock_user},
                          supabase=db_d)

    r = _del('row-draft')
    check('delete_draft → ok=True', r.get('ok') is True)
    check('delete: 1건 DELETE 발생', len(db_d.deletes) == 1)

    # 필터 검증 — status='draft' eq 조건 포함 여부
    del_filters = db_d.deletes[0][1]
    has_user_id  = any(c == 'user_id'  and v == 'u-test'  for _, c, v in del_filters)
    has_draft_id = any(c == 'id'       and v == 'row-draft' for _, c, v in del_filters)
    has_status   = any(c == 'status'   and v == 'draft'   for _, c, v in del_filters)
    check("DELETE 필터: user_id='u-test' 포함",  has_user_id)
    check("DELETE 필터: id='row-draft' 포함",    has_draft_id)
    check("DELETE 필터: status='draft' 한정",    has_status)

except Exception as e:
    check('blog_delete_draft 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 8. instagram get/delete — 동일 접근 제어
# ──────────────────────────────────────────────────────────────
print('\n[8] instagram_get_draft / instagram_delete_draft')

try:
    _INSTA_ALL_ROWS = [
        {'id': 'i-draft', 'user_id': 'u-test', 'creation_type': 'instagram',
         'status': 'draft', 'step_reached': 2,
         'input_data': {}, 'step_data': {}, 'output_data': {}},
        {'id': 'i-done',  'user_id': 'u-test', 'creation_type': 'instagram',
         'status': 'done',  'step_reached': 5,
         'input_data': {}, 'step_data': {}, 'output_data': {}},
        {'id': 'i-gen',   'user_id': 'u-test', 'creation_type': 'instagram',
         'status': 'generating', 'step_reached': 4,
         'input_data': {}, 'step_data': {}, 'output_data': {}},
    ]
    db_ig = _FakeDB({'creations': _INSTA_ALL_ROWS})

    _insta_get_fn  = _unwrap(insta_mod.instagram_get_draft)
    def _iget(row_id):
        return _call_view(test_app,
                          lambda: _insta_get_fn(row_id),
                          f'/create/instagram/draft/{row_id}',
                          patches={'blueprints.create.instagram.current_user': _mock_user},
                          supabase=db_ig)

    check('insta get_draft(draft) → ok=True',  _iget('i-draft').get('ok') is True)
    check('insta get_draft(done)  → ok=True',  _iget('i-done').get('ok') is True)
    check('insta get_draft(generating) → ok=False', _iget('i-gen').get('ok') is False)

    db_id = _FakeDB({'creations': _INSTA_ALL_ROWS})

    _insta_del_fn  = _unwrap(insta_mod.instagram_delete_draft)
    def _idel(row_id):
        return _call_view(test_app,
                          lambda: _insta_del_fn(row_id),
                          f'/create/instagram/draft/{row_id}', method='DELETE',
                          patches={'blueprints.create.instagram.current_user': _mock_user},
                          supabase=db_id)

    _idel('i-draft')
    del_filters_i = db_id.deletes[0][1]
    check('insta DELETE 필터: status=draft 포함',
          any(c == 'status' and v == 'draft' for _, c, v in del_filters_i))
    check('insta DELETE 필터: user_id 포함',
          any(c == 'user_id' and v == 'u-test' for _, c, v in del_filters_i))

except Exception as e:
    check('instagram_get/delete_draft 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 9. operator_id 전파 — save_draft에서 operator 멤버가 저장 시
# ──────────────────────────────────────────────────────────────
print('\n[9] operator_id 전파 — 팀 멤버 draft 저장')

try:
    _mock_op_user = MagicMock()
    _mock_op_user.id = 'u-member'
    _mock_op_user.operator_id = 'op-1'

    db_op = _FakeDB({'creations': []})
    _OP_SAVE_PAYLOAD = {
        'draft_id': None, 'step_reached': 2,
        'input_data': {'brand_id': 'b-1', 'direction': '팀 방향'},
        'step_data': {}, 'output_data': {},
    }

    _blog_save_fn = _unwrap(blog_mod.blog_save_draft)
    data_op = _call_view(test_app, _blog_save_fn,
                         '/create/blog/save-draft', method='POST',
                         json_body=_OP_SAVE_PAYLOAD,
                         patches={'blueprints.create.blog.current_user': _mock_op_user},
                         supabase=db_op)

    check('팀 멤버 save_draft → ok=True', data_op.get('ok') is True)
    ins_op = db_op.inserts[0][1]
    check('INSERT: operator_id=op-1 포함', ins_op.get('operator_id') == 'op-1')
    check('INSERT: user_id=u-member 포함', ins_op.get('user_id') == 'u-member')

    # 개인 유저 → operator_id 키 없거나 None
    db_personal = _FakeDB({'creations': []})
    _call_view(test_app, _blog_save_fn,
               '/create/blog/save-draft', method='POST',
               json_body=_OP_SAVE_PAYLOAD,
               patches={'blueprints.create.blog.current_user': _mock_user},
               supabase=db_personal)

    ins_personal = db_personal.inserts[0][1]
    check('개인 유저 INSERT: operator_id 없거나 None',
          ins_personal.get('operator_id') is None
          or 'operator_id' not in ins_personal)

except Exception as e:
    check('operator_id 전파 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 10. templates/create/blog.html — 배너 마크업 + JS 함수
# ──────────────────────────────────────────────────────────────
print('\n[10] templates/create/blog.html — 배너 & JS 검증')

try:
    blog_tpl_path = os.path.join(ROOT, 'templates', 'create', 'blog.html')
    check('blog.html 파일 존재', os.path.isfile(blog_tpl_path))
    blog_tpl = open(blog_tpl_path, encoding='utf-8').read()

    # 배너 마크업
    check("배너: {% if blog_drafts %} 조건 분기",
          '{% if blog_drafts' in blog_tpl or '{% if blog_drafts %}' in blog_tpl)
    check("배너: 📝 이어서 작업하기 텍스트",
          '이어서 작업하기' in blog_tpl)
    check("배너: draftCard_{{ d.id }} ID",
          'draftCard_' in blog_tpl)
    check("배너: loadBlogDraft 호출",
          "onclick=\"loadBlogDraft" in blog_tpl)
    check("배너: deleteBlogDraft 호출",
          "onclick=\"deleteBlogDraft" in blog_tpl)
    check("배너: 🔄 완성된 작업 재작업 섹션",
          '완성된 작업 재작업' in blog_tpl or '이미지 재작업' in blog_tpl)
    check("배너: redoBlogImages 호출",
          "onclick=\"redoBlogImages" in blog_tpl)

    # JS 전역 상태
    check("JS: let draftId 선언",
          'let draftId' in blog_tpl)

    # JS 함수 정의
    check("JS: async function saveBlogDraft",
          'async function saveBlogDraft' in blog_tpl)
    check("JS: async function loadBlogDraft",
          'async function loadBlogDraft' in blog_tpl)
    check("JS: async function deleteBlogDraft",
          'async function deleteBlogDraft' in blog_tpl)
    check("JS: async function redoBlogImages",
          'async function redoBlogImages' in blog_tpl)

    # 자동저장 호출 위치
    check("Step 2: selectAngle에서 saveBlogDraft(2) 호출",
          'saveBlogDraft(2)' in blog_tpl)
    check("Step 3: generateBlogText 성공 후 saveBlogDraft(3) 호출",
          'saveBlogDraft(3)' in blog_tpl)

    # save-draft URL
    check("blog_save_draft url_for 사용",
          "url_for(\"create.blog_save_draft\")" in blog_tpl
          or "url_for('create.blog_save_draft')" in blog_tpl)
    check("blog_get_draft url_for 사용",
          "blog_get_draft" in blog_tpl)
    check("blog_delete_draft url_for 사용",
          "blog_delete_draft" in blog_tpl)

    # draft fetch에 CSRF 헤더 포함
    check("saveBlogDraft: X-CSRFToken CSRF 헤더",
          "X-CSRFToken" in blog_tpl and 'saveBlogDraft' in blog_tpl)

    # draftId 업데이트 로직
    check("saveBlogDraft: if data.ok → draftId 갱신",
          'draftId = data.draft_id' in blog_tpl)

    # loadBlogDraft: goStep 호출
    check("loadBlogDraft: goStep 호출",
          'goStep(targetStep)' in blog_tpl or 'goStep(' in blog_tpl.split('async function loadBlogDraft')[1][:1000])

except Exception as e:
    check('blog.html 템플릿 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 11. templates/create/instagram.html — 배너 마크업 + JS 함수
# ──────────────────────────────────────────────────────────────
print('\n[11] templates/create/instagram.html — 배너 & JS 검증')

try:
    insta_tpl_path = os.path.join(ROOT, 'templates', 'create', 'instagram.html')
    check('instagram.html 파일 존재', os.path.isfile(insta_tpl_path))
    insta_tpl = open(insta_tpl_path, encoding='utf-8').read()

    # 배너 마크업
    check("배너: {% if insta_drafts %} 조건 분기",
          'insta_drafts' in insta_tpl)
    check("배너: 📝 이어서 작업하기 텍스트",
          '이어서 작업하기' in insta_tpl)
    check("배너: instaDraftCard_{{ d.id }} ID",
          'instaDraftCard_' in insta_tpl)
    check("배너: loadInstaDraft 호출",
          "onclick=\"loadInstaDraft" in insta_tpl)
    check("배너: deleteInstaDraft 호출",
          "onclick=\"deleteInstaDraft" in insta_tpl)
    check("배너: redoInstaImages 호출",
          "onclick=\"redoInstaImages" in insta_tpl)

    # JS 전역 상태
    check("JS: let draftId 선언",
          'let draftId' in insta_tpl)

    # JS 함수 정의
    check("JS: async function saveInstaDraft",
          'async function saveInstaDraft' in insta_tpl)
    check("JS: async function loadInstaDraft",
          'async function loadInstaDraft' in insta_tpl)
    check("JS: async function deleteInstaDraft",
          'async function deleteInstaDraft' in insta_tpl)
    check("JS: async function redoInstaImages",
          'async function redoInstaImages' in insta_tpl)

    # 자동저장 호출 위치
    check("Step 2: selectAngle에서 saveInstaDraft(2) 호출",
          'saveInstaDraft(2)' in insta_tpl)
    check("Step 3: generateCaption 성공 후 saveInstaDraft(3) 호출",
          'saveInstaDraft(3)' in insta_tpl)

    # URL / CSRF
    check("instagram_save_draft url_for 사용",
          "instagram_save_draft" in insta_tpl)
    check("instagram_get_draft url_for 사용",
          "instagram_get_draft" in insta_tpl)
    check("instagram_delete_draft url_for 사용",
          "instagram_delete_draft" in insta_tpl)
    check("saveInstaDraft: draftId 갱신 로직",
          'draftId = data.draft_id' in insta_tpl)

    # captionData / hashtagText 저장 여부 (saveInstaDraft 함수 본문 3000자 검색)
    check("step_data에 caption_short 포함",
          'caption_short' in insta_tpl.split('async function saveInstaDraft')[1][:3000])
    check("step_data에 hashtag_text 포함",
          'hashtag_text' in insta_tpl.split('async function saveInstaDraft')[1][:3000])

    # loadInstaDraft: captionData 복원 (함수 본문 3000자 검색)
    check("loadInstaDraft: captionData 복원",
          'captionData' in insta_tpl.split('async function loadInstaDraft')[1][:3000])

    # goStep 호출 (함수 본문 3000자 검색)
    check("loadInstaDraft: goStep 호출",
          'goStep(' in insta_tpl.split('async function loadInstaDraft')[1][:3000])

except Exception as e:
    check('instagram.html 템플릿 검증', False, repr(e)[:140])


# ──────────────────────────────────────────────────────────────
# 12. 소스 코드 구조 검사 — blog.py / instagram.py
# ──────────────────────────────────────────────────────────────
print('\n[12] 소스 코드 구조 검사')

blog_src  = open(os.path.join(ROOT, 'blueprints', 'create', 'blog.py'), encoding='utf-8').read()
insta_src = open(os.path.join(ROOT, 'blueprints', 'create', 'instagram.py'), encoding='utf-8').read()

check("blog.py: _get_blog_drafts 함수 정의",
      'def _get_blog_drafts(' in blog_src)
check("blog.py: step_reached 컬럼 사용",
      'step_reached' in blog_src)
check("blog.py: step_data 컬럼 사용",
      'step_data' in blog_src)
check("blog.py: updated_at 기록",
      'updated_at' in blog_src)
check("blog.py: status='draft' 세팅",
      "'status':        'draft'" in blog_src or '"status": "draft"' in blog_src)
check("blog.py: uuid4() 사용 (신규 ID 생성)",
      'uuid4()' in blog_src)
check("blog.py: blog_save_draft 라우트",
      "'/blog/save-draft'" in blog_src)
check("blog.py: blog_get_draft 라우트 (GET)",
      "'/blog/draft/<draft_id>'" in blog_src and 'GET' in blog_src)
check("blog.py: blog_delete_draft 라우트 (DELETE)",
      "'/blog/draft/<draft_id>'" in blog_src and 'DELETE' in blog_src)
check("blog.py: status in ('draft','done') 접근 제어",
      "('draft', 'done')" in blog_src or "('draft','done')" in blog_src)
check("blog.py: 블로그 라우트에 blog_drafts= 전달",
      'blog_drafts=' in blog_src)

check("instagram.py: _get_instagram_drafts 함수 정의",
      'def _get_instagram_drafts(' in insta_src)
check("instagram.py: status='draft' 세팅",
      "'status':        'draft'" in insta_src or "'draft'" in insta_src)
check("instagram.py: instagram_save_draft 라우트",
      "'/instagram/save-draft'" in insta_src)
check("instagram.py: instagram_get_draft 라우트 (GET)",
      "'/instagram/draft/<draft_id>'" in insta_src)
check("instagram.py: instagram_delete_draft 라우트 (DELETE)",
      "DELETE" in insta_src and "instagram/draft" in insta_src)
check("instagram.py: 인스타 라우트에 insta_drafts= 전달",
      'insta_drafts=' in insta_src)


# ──────────────────────────────────────────────────────────────
# 결과 요약
# ──────────────────────────────────────────────────────────────
print('\n' + '═' * 65)
total  = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed

print(f'\n총 {total}건 — \033[92mPASS {passed}\033[0m / \033[91mFAIL {failed}\033[0m')

if failed:
    print('\n실패 항목:')
    for label, ok in results:
        if not ok:
            print(f'  ✗  {label}')

print('═' * 65)
sys.exit(0 if failed == 0 else 1)
