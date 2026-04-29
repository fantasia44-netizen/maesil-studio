"""시스템 설정 조회 — 환경변수 우선, 없으면 saas_config DB에서 읽기"""
import os
import logging

logger = logging.getLogger(__name__)

_ENV_MAP = {
    'anthropic_api_key':     'ANTHROPIC_API_KEY',
    'openai_api_key':        'OPENAI_API_KEY',
    'fal_api_key':           'FAL_KEY',
    'ideogram_api_key':      'IDEOGRAM_API_KEY',
    'portone_api_secret':    'PORTONE_API_SECRET',
    'portone_store_id':      'PORTONE_STORE_ID',
    'portone_channel_card':  'PORTONE_CHANNEL_KEY_CARD',
    'portone_channel_kakao': 'PORTONE_CHANNEL_KEY_KAKAO',
    'image_provider':        'IMAGE_PROVIDER',
    'smtp_host':             'SMTP_HOST',
    'smtp_port':             'SMTP_PORT',
    'smtp_user':             'SMTP_USER',
    'smtp_password':         'SMTP_PASSWORD',
    'smtp_from':             'SMTP_FROM',
}


def get_config(key: str) -> str:
    """설정값 조회 — 환경변수 우선, 없으면 saas_config DB"""
    env_key = _ENV_MAP.get(key, key.upper())
    val = os.environ.get(env_key, '')
    if val:
        return val

    try:
        from flask import current_app
        supabase = current_app.supabase
        if not supabase:
            return ''
        row = supabase.table('saas_config').select(
            'value_text, value_secret'
        ).eq('key', key).execute()
        if not row.data:
            return ''
        if row.data[0].get('value_secret'):
            from services.crypto import decrypt_value
            return decrypt_value(row.data[0]['value_secret'])
        return row.data[0].get('value_text', '')
    except Exception as e:
        logger.warning(f'[CONFIG] {key} 조회 실패: {e}')
        return ''
