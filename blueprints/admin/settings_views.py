"""어드민 - 시스템 설정 (API 키 / DB 연결 등)"""
import logging
from flask import render_template, request, jsonify, current_app
from flask_login import login_required
from blueprints.admin import admin_bp
from models import require_superadmin
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)

CONFIG_KEYS = [
    # (key, label, type, description, testable)
    ('anthropic_api_key',     'Claude API Key',         'secret', 'Anthropic Claude 텍스트 생성',  True),
    ('fal_api_key',           'fal.ai API Key',         'secret', 'FLUX 이미지 생성',              True),
    ('ideogram_api_key',      'Ideogram API Key',       'secret', '한글 텍스트 이미지 생성',       True),
    ('openai_api_key',        'OpenAI API Key',         'secret', 'DALL-E / GPT (선택)',            False),
    ('portone_api_secret',    'PortOne API Secret',     'secret', '결제 API',                      False),
    ('portone_store_id',      'PortOne Store ID',       'text',   '',                              False),
    ('portone_channel_card',  'PortOne 카드 채널 키',   'text',   '',                              False),
    ('portone_channel_kakao', 'PortOne 카카오 채널 키', 'text',   '',                              False),
    ('image_provider',        '이미지 생성 엔진',        'text',   'dalle | flux | ideogram',       False),
    ('smtp_host',             'SMTP 호스트',             'text',   '이메일 발송',                   False),
    ('smtp_port',             'SMTP 포트',               'text',   '',                              False),
    ('smtp_user',             'SMTP 사용자',             'text',   '',                              False),
    ('smtp_password',         'SMTP 비밀번호',           'secret', '',                              False),
    ('smtp_from',             'SMTP 발신자',             'text',   '',                              False),
]

TESTABLE_KEYS = {c[0] for c in CONFIG_KEYS if c[4]}


def _get_all_configs(supabase) -> dict:
    try:
        rows = supabase.table('saas_config').select('key, value_text, value_secret').execute()
        result = {}
        for row in (rows.data or []):
            k = row['key']
            if row.get('value_secret'):
                result[k] = '••••••••'
            else:
                result[k] = row.get('value_text', '')
        return result
    except Exception as e:
        logger.error(f'[ADMIN] get_configs error: {e}')
        return {}


def _upsert_config(supabase, key: str, value: str, is_secret: bool):
    from services.crypto import encrypt_value
    payload = {'key': key, 'updated_at': now_kst().isoformat()}
    if is_secret:
        payload['value_secret'] = encrypt_value(value) if value else ''
        payload['value_text'] = ''
    else:
        payload['value_text'] = value
        payload['value_secret'] = ''

    existing = supabase.table('saas_config').select('id').eq('key', key).execute()
    if existing.data:
        supabase.table('saas_config').update(payload).eq('key', key).execute()
    else:
        payload['created_at'] = now_kst().isoformat()
        supabase.table('saas_config').insert(payload).execute()


@admin_bp.route('/settings')
@login_required
@require_superadmin
def settings():
    supabase = current_app.supabase
    configs = _get_all_configs(supabase)
    return render_template('admin/settings.html',
                           CONFIG_KEYS=CONFIG_KEYS,
                           configs=configs)


@admin_bp.route('/settings/save-key', methods=['POST'])
@login_required
@require_superadmin
def save_key():
    supabase = current_app.supabase
    data = request.json or {}
    key = data.get('key', '').strip()
    value = data.get('value', '').strip()

    if not key:
        return jsonify(ok=False, message='키가 없습니다.')
    if value == '••••••••':
        return jsonify(ok=False, message='변경된 값이 없습니다.')

    cfg = next((c for c in CONFIG_KEYS if c[0] == key), None)
    if not cfg:
        return jsonify(ok=False, message='허용되지 않는 키입니다.')

    try:
        _upsert_config(supabase, key, value, is_secret=(cfg[2] == 'secret'))
        return jsonify(ok=True, message='저장되었습니다.')
    except Exception as e:
        logger.error(f'[ADMIN] save_key error ({key}): {e}')
        return jsonify(ok=False, message=f'저장 실패: {e}')


@admin_bp.route('/settings/test/<key>', methods=['POST'])
@login_required
@require_superadmin
def test_key(key):
    if key not in TESTABLE_KEYS:
        return jsonify(ok=False, message='테스트할 수 없는 키입니다.')

    # 저장된 실제 값 읽기
    from services.config_service import get_config
    try:
        if key == 'anthropic_api_key':
            return _test_anthropic(get_config('anthropic_api_key'))
        elif key == 'fal_api_key':
            return _test_fal(get_config('fal_api_key'))
        elif key == 'ideogram_api_key':
            return _test_ideogram(get_config('ideogram_api_key'))
    except Exception as e:
        return jsonify(ok=False, message=f'오류: {e}')

    return jsonify(ok=False, message='테스트 구현 없음')


def _test_anthropic(api_key: str):
    if not api_key:
        return jsonify(ok=False, message='API 키가 설정되지 않았습니다.')
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=10,
            messages=[{'role': 'user', 'content': 'hi'}],
        )
        return jsonify(ok=True, message=f'연결 성공 — 모델: {msg.model}')
    except Exception as e:
        return jsonify(ok=False, message=f'연결 실패: {e}')


def _test_fal(api_key: str):
    if not api_key:
        return jsonify(ok=False, message='API 키가 설정되지 않았습니다.')
    try:
        import requests as req_lib
        r = req_lib.post(
            'https://fal.run/fal-ai/flux/schnell',
            headers={'Authorization': f'Key {api_key}', 'Content-Type': 'application/json'},
            json={
                'prompt': 'a red apple',
                'image_size': {'width': 256, 'height': 256},
                'num_images': 1,
                'num_inference_steps': 1,
            },
            timeout=30,
        )
        if r.status_code == 200:
            return jsonify(ok=True, message='연결 성공 — 이미지 생성 가능')
        elif r.status_code in (400, 422):
            return jsonify(ok=True, message='인증 성공 — 키 유효')
        elif r.status_code == 401:
            return jsonify(ok=False, message='인증 실패 — 키를 확인하세요.')
        elif r.status_code == 403:
            # 키는 유효하나 해당 모델 접근 제한 (플랜 문제일 수 있음)
            return jsonify(ok=True, message=f'키 인식됨 — 모델 접근 제한(403), fal.ai 플랜 확인 필요')
        else:
            return jsonify(ok=False, message=f'HTTP {r.status_code}: {r.text[:120]}')
    except Exception as e:
        return jsonify(ok=False, message=f'연결 실패: {e}')


def _test_ideogram(api_key: str):
    if not api_key:
        return jsonify(ok=False, message='API 키가 설정되지 않았습니다.')
    try:
        import requests as req_lib
        # 빈 body → 인증 체크만 (400/422 = 키 유효, 401 = 키 불량)
        r = req_lib.post(
            'https://api.ideogram.ai/generate',
            headers={'Api-Key': api_key, 'Content-Type': 'application/json'},
            json={'image_request': {}},
            timeout=(5, 8),  # (connect, read)
        )
        if r.status_code in (200, 201):
            return jsonify(ok=True, message='연결 성공')
        elif r.status_code in (400, 422):
            return jsonify(ok=True, message='인증 성공 — 키 유효')
        elif r.status_code == 401:
            return jsonify(ok=False, message='인증 실패 — 키를 확인하세요.')
        elif r.status_code == 402:
            return jsonify(ok=True, message='키 유효 — 크레딧 부족')
        else:
            return jsonify(ok=False, message=f'HTTP {r.status_code}: {r.text[:100]}')
    except Exception as e:
        return jsonify(ok=False, message=f'연결 실패: {e}')
