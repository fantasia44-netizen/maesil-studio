"""매실 인사이트 외부 API 연동 검증 하네스.

검증 대상:
  - migrations/001_external_integrations.sql 파싱 (DDL 존재)
  - services/maesil_insight_client.py: URL/헤더 구성, 에러 매핑, 친절 메시지
  - services/maesil_insight_connection.py: 임포트/시그니처
  - blueprints/integrations.py: 임포트, 라우트 등록
  - 템플릿 존재 + 핵심 마크업
  - app.py 에 integrations_bp 등록 코드 존재

Supabase 미연결 환경에서도 통과 (모킹 + 순수 함수 단위).
실행: py -3 -X utf8 test_integrations_harness.py
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = '\033[92m PASS\033[0m'
FAIL = '\033[91m FAIL\033[0m'
results = []


def check(label, condition, detail=''):
    mark = PASS if condition else FAIL
    print(f'{mark}  {label}' + (f'  [{detail}]' if detail else ''))
    results.append((label, condition))


# ──────────────────────────────────────────────────────
# 환경 (Supabase 없이 임포트 가능)
# ──────────────────────────────────────────────────────
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-key')
os.environ.setdefault('SUPABASE_URL', 'https://placeholder.supabase.co')
os.environ.setdefault('SUPABASE_SERVICE_KEY', 'placeholder')
os.environ.setdefault('ENCRYPTION_KEY', 'a' * 32)


# ──────────────────────────────────────────────────────
# 1. 마이그레이션
# ──────────────────────────────────────────────────────
print('\n[1] migrations/001_external_integrations.sql')
mig = os.path.join(ROOT, 'migrations', '001_external_integrations.sql')
check('파일 존재', os.path.isfile(mig))
if os.path.isfile(mig):
    sql = open(mig, encoding='utf-8').read()
    check('ALTER TABLE products ... source',
          'ALTER TABLE products' in sql and "source     TEXT NOT NULL" in sql)
    check('ALTER TABLE products ... source_ref', 'source_ref TEXT NOT NULL' in sql)
    check('ALTER TABLE products ... image_url',  'image_url  TEXT NOT NULL' in sql)
    check('idx_products_source_ref unique 부분 인덱스',
          'idx_products_source_ref' in sql and 'UNIQUE INDEX' in sql)
    check('CREATE TABLE maesil_insight_connections',
          'CREATE TABLE IF NOT EXISTS maesil_insight_connections' in sql)
    check('user_id UNIQUE FK ON DELETE CASCADE',
          'UNIQUE REFERENCES users(id) ON DELETE CASCADE' in sql)
    check('token_encrypted NOT NULL', 'token_encrypted        TEXT NOT NULL' in sql)


# ──────────────────────────────────────────────────────
# 2. HTTP 클라이언트 — 헤더/URL 구성, 에러 매핑
# ──────────────────────────────────────────────────────
print('\n[2] services.maesil_insight_client')
try:
    from services.maesil_insight_client import (
        MaesilInsightClient, MaesilInsightError, friendly_error_message,
        DEFAULT_BASE,
    )
    check('import client', True)

    # 빈 토큰 거부
    raised = False
    try:
        MaesilInsightClient('')
    except ValueError:
        raised = True
    check("빈 토큰 → ValueError", raised)

    # 헤더 구성
    c = MaesilInsightClient('mi_test_token_value')
    check("Authorization 헤더 'Bearer mi_...'",
          c.headers['Authorization'] == 'Bearer mi_test_token_value')
    check("X-Source 헤더 'maesil-creator'",
          c.headers['X-Source'] == 'maesil-creator')
    check("Accept JSON", c.headers['Accept'] == 'application/json')
    check("base URL 기본값 운영 도메인",
          'maesil-insight.com' in DEFAULT_BASE)

    # base URL override
    c2 = MaesilInsightClient('mi_x', base_url='http://localhost:5000/api/v1/external/')
    check("base URL trailing slash 정리",
          c2.base == 'http://localhost:5000/api/v1/external')

    # ─── verify(): mocked 200 ───
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        'operator_id': 'op-uuid', 'operator_name': '배마마',
        'plan': 'professional', 'scopes': ['products:read'],
    }
    with patch('services.maesil_insight_client.requests.get',
               return_value=fake_resp) as mock_get:
        out = c.verify()
        check("verify() 200 → dict 반환", out['operator_name'] == '배마마')
        # 호출 인자 검증
        call_args = mock_get.call_args
        url_arg = call_args.args[0] if call_args.args else call_args.kwargs.get('url', '')
        check("verify() URL 끝이 '/me'", url_arg.endswith('/me'),
              url_arg[-30:])
        headers_arg = call_args.kwargs.get('headers', {})
        check("verify() Authorization 헤더 포함",
              headers_arg.get('Authorization') == 'Bearer mi_test_token_value')

    # ─── 401 응답 → MaesilInsightError('unauthorized', 401) ───
    err_resp = MagicMock()
    err_resp.status_code = 401
    err_resp.json.return_value = {'error': 'unauthorized', 'detail': 'invalid token'}
    with patch('services.maesil_insight_client.requests.get', return_value=err_resp):
        try:
            c.verify()
            check('401 → MaesilInsightError', False, 'no exception')
        except MaesilInsightError as e:
            check('401 → MaesilInsightError', True)
            check("e.code == 'unauthorized'", e.code == 'unauthorized')
            check('e.status == 401', e.status == 401)

    # ─── 429 + Retry-After ───
    rl_resp = MagicMock()
    rl_resp.status_code = 429
    rl_resp.json.return_value = {'error': 'rate_limited', 'detail': '60/min'}
    with patch('services.maesil_insight_client.requests.get', return_value=rl_resp):
        try:
            c.verify()
            check('429 → MaesilInsightError', False)
        except MaesilInsightError as e:
            check("429 → e.code == 'rate_limited'", e.code == 'rate_limited')

    # ─── 네트워크 타임아웃 ───
    import requests as _r
    with patch('services.maesil_insight_client.requests.get',
               side_effect=_r.Timeout()):
        try:
            c.verify()
            check('Timeout → MaesilInsightError', False)
        except MaesilInsightError as e:
            check("Timeout → e.code == 'timeout'", e.code == 'timeout')
            check('Timeout → e.status == 0', e.status == 0)

    # ─── list_products params 전달 ───
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = {'products': [], 'pagination': {}}
    with patch('services.maesil_insight_client.requests.get',
               return_value=list_resp) as mock_get:
        c.list_products(page=2, per_page=20, keyword='사과', category='과일')
        params = mock_get.call_args.kwargs.get('params', {})
        check("list_products: page/per_page 전달",
              params.get('page') == 2 and params.get('per_page') == 20)
        check("list_products: keyword 전달", params.get('keyword') == '사과')
        check("list_products: category 전달", params.get('category') == '과일')
        # None 값은 제외
        check("None 값은 params 에서 제외 (channel/status/sort)",
              'channel' not in params and 'status' not in params and 'sort' not in params)

    # ─── get_product 빈 ID 거부 ───
    raised = False
    try:
        c.get_product('')
    except ValueError:
        raised = True
    check('get_product 빈 ID → ValueError', raised)

    # ─── friendly_error_message ───
    msg = friendly_error_message(MaesilInsightError('unauthorized', 401, 'x'))
    check("friendly_error_message(unauthorized) 한국어 메시지",
          '토큰' in msg)
    msg = friendly_error_message(MaesilInsightError('rate_limited', 429, ''))
    check("friendly_error_message(rate_limited) 한국어 메시지",
          '잠시' in msg or '많' in msg)
    msg = friendly_error_message(MaesilInsightError('timeout', 0, ''))
    check("friendly_error_message(timeout) 한국어 메시지",
          '지연' in msg or '시간' in msg)

except Exception as e:
    check('client 모듈 검증', False, repr(e)[:120])


# ──────────────────────────────────────────────────────
# 3. connection 헬퍼 — 임포트 + 시그니처
# ──────────────────────────────────────────────────────
print('\n[3] services.maesil_insight_connection')
try:
    from services import maesil_insight_connection as conn_mod
    check('import connection 모듈', True)

    for name in ('get_connection', 'get_connection_token',
                 'get_client_for_user', 'is_connected',
                 'save_connection', 'disconnect',
                 'mark_used', 'mark_error', 'verify_and_save'):
        check(f'함수 정의: {name}', callable(getattr(conn_mod, name, None)))

    # _token_prefix
    check("_token_prefix('mi_abcdefghij_extra') == 'mi_abcdefgh'",
          conn_mod._token_prefix('mi_abcdefghij_extra') == 'mi_abcdefgh')
    check("_token_prefix('') == ''", conn_mod._token_prefix('') == '')

    # verify_and_save: 빈 토큰 거부
    raised = False
    try:
        conn_mod.verify_and_save('user-x', '   ')
    except ValueError:
        raised = True
    check('verify_and_save 빈 토큰 → ValueError', raised)
except Exception as e:
    check('connection 모듈 검증', False, repr(e)[:120])


# ──────────────────────────────────────────────────────
# 4. blueprint — 라우트 등록
# ──────────────────────────────────────────────────────
print('\n[4] blueprints.integrations 라우트 wiring')
try:
    from flask import Flask
    from blueprints.integrations import integrations_bp

    test_app = Flask(__name__)
    test_app.config['SECRET_KEY'] = 'test'
    test_app.config['WTF_CSRF_ENABLED'] = False
    test_app.register_blueprint(integrations_bp)

    rules = {(str(r.rule), tuple(sorted(r.methods - {'HEAD', 'OPTIONS'})))
             for r in test_app.url_map.iter_rules()}
    rule_strs = {r[0] for r in rules}

    check('GET /integrations/', '/integrations/' in rule_strs)
    check('POST /integrations/connect',
          ('/integrations/connect', ('POST',)) in rules)
    check('POST /integrations/disconnect',
          ('/integrations/disconnect', ('POST',)) in rules)
    check('GET /integrations/import',
          any(s == '/integrations/import' for s in rule_strs))
    # GET + POST 둘 다 등록되어야 함
    methods_for_import = {m for s, ms in rules
                          if s == '/integrations/import' for m in ms}
    check("/integrations/import 가 GET/POST 모두 지원",
          'GET' in methods_for_import and 'POST' in methods_for_import,
          str(sorted(methods_for_import)))
except Exception as e:
    check('blueprint 라우트 검증', False, repr(e)[:120])


# ──────────────────────────────────────────────────────
# 5. app.py wiring
# ──────────────────────────────────────────────────────
print('\n[5] app.py wiring')
try:
    src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    check('app.py 에 integrations_bp import',
          'from blueprints.integrations import integrations_bp' in src)
    check('app.register_blueprint(integrations_bp)',
          'app.register_blueprint(integrations_bp)' in src)
except Exception as e:
    check('app.py 검증', False, repr(e)[:120])


# ──────────────────────────────────────────────────────
# 6. 템플릿
# ──────────────────────────────────────────────────────
print('\n[6] 템플릿')
for name in ('integrations/index.html', 'integrations/import.html'):
    p = os.path.join(ROOT, 'templates', name)
    check(f'templates/{name} 존재', os.path.isfile(p))

idx = open(os.path.join(ROOT, 'templates', 'integrations', 'index.html'),
           encoding='utf-8').read()
check("index.html: connect/disconnect 폼 모두 존재",
      "url_for('integrations.connect')" in idx and
      "url_for('integrations.disconnect')" in idx)
check("index.html: token_prefix 마스킹",
      'token_prefix' in idx and '****' in idx)

imp = open(os.path.join(ROOT, 'templates', 'integrations', 'import.html'),
           encoding='utf-8').read()
check("import.html: import_apply POST 폼",
      "url_for('integrations.import_apply')" in imp)
check("import.html: 중복(already) 분기 표시",
      'is_dup' in imp and 'already' in imp)
check("import.html: 페이지네이션 마크업",
      'pagination' in imp and 'total_pages' in imp)

# settings.html 에 외부연동 카드 링크
settings_tpl = open(os.path.join(ROOT, 'templates', 'main', 'settings.html'),
                    encoding='utf-8').read()
check("settings.html 에 외부 연동 링크 추가",
      "url_for('integrations.index')" in settings_tpl)

# product/edit.html 에 가져오기 버튼
edit_tpl = open(os.path.join(ROOT, 'templates', 'product', 'edit.html'),
                encoding='utf-8').read()
check("product/edit.html 에 'integrations.import_list' 버튼",
      "url_for('integrations.import_list')" in edit_tpl)


# ──────────────────────────────────────────────────────
# 7. .env.example
# ──────────────────────────────────────────────────────
print('\n[7] .env.example')
env_tpl = open(os.path.join(ROOT, '.env.example'), encoding='utf-8').read()
check('MAESIL_INSIGHT_BASE 설명 포함',
      'MAESIL_INSIGHT_BASE' in env_tpl)


# ──────────────────────────────────────────────────────
# 결과
# ──────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed
print(f'\n{"="*60}')
print(f'결과: {passed}/{total} PASS' +
      (f'  ({failed} FAIL)' if failed else '  — 전체 통과'))
print('=' * 60)
sys.exit(0 if failed == 0 else 1)
