"""어드민 - 시스템 설정 (API 키 / DB 연결 등)"""
import logging
from flask import render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required
from blueprints.admin import admin_bp
from models import require_superadmin
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)

# 관리 가능한 설정 키 목록
CONFIG_KEYS = [
    # (key, label, type, description)
    ('anthropic_api_key',       'Claude API Key',              'secret', 'Anthropic Claude 텍스트 생성'),
    ('openai_api_key',          'OpenAI API Key',              'secret', 'DALL-E / GPT Image 이미지 생성'),
    ('fal_api_key',             'fal.ai API Key',              'secret', 'FLUX 이미지 생성'),
    ('ideogram_api_key',        'Ideogram API Key',            'secret', '한글 텍스트 이미지 생성'),
    ('portone_api_secret',      'PortOne API Secret',          'secret', '결제 API'),
    ('portone_store_id',        'PortOne Store ID',            'text',   ''),
    ('portone_channel_card',    'PortOne 카드 채널 키',         'text',   ''),
    ('portone_channel_kakao',   'PortOne 카카오 채널 키',       'text',   ''),
    ('image_provider',          '이미지 생성 엔진',              'text',   'dalle | flux | ideogram'),
    ('smtp_host',               'SMTP 호스트',                  'text',   '이메일 발송'),
    ('smtp_port',               'SMTP 포트',                    'text',   ''),
    ('smtp_user',               'SMTP 사용자',                  'text',   ''),
    ('smtp_password',           'SMTP 비밀번호',                'secret', ''),
    ('smtp_from',               'SMTP 발신자',                  'text',   ''),
]


def _get_all_configs(supabase) -> dict:
    try:
        rows = supabase.table('saas_config').select('key, value_text, value_secret').execute()
        result = {}
        for row in (rows.data or []):
            k = row['key']
            if row.get('value_secret'):
                result[k] = '••••••••'  # 마스킹
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


@admin_bp.route('/settings/save', methods=['POST'])
@login_required
@require_superadmin
def save_settings():
    supabase = current_app.supabase
    saved = 0
    for key, label, type_, _ in CONFIG_KEYS:
        value = request.form.get(key, '').strip()
        if not value:
            continue
        if value == '••••••••':
            continue  # 마스킹된 값은 변경 안 함
        try:
            _upsert_config(supabase, key, value, is_secret=(type_ == 'secret'))
            saved += 1
        except Exception as e:
            logger.error(f'[ADMIN] save_settings error ({key}): {e}')

    if saved > 0:
        flash(f'{saved}개 설정이 저장되었습니다.', 'success')
    else:
        flash('변경된 값이 없습니다.', 'info')
    return redirect(url_for('admin.settings'))
