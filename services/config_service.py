"""시스템 설정 조회 — 환경변수 우선, 없으면 saas_config DB에서 읽기"""
import os
import logging

logger = logging.getLogger(__name__)

_ENV_MAP = {
    'anthropic_api_key':     'ANTHROPIC_API_KEY',
    'openai_api_key':        'OPENAI_API_KEY',
    'fal_api_key':           'FAL_KEY',
    'ideogram_api_key':      'IDEOGRAM_API_KEY',
    'maeyo_agency_url':      'MAEYO_AGENCY_URL',
    'maeyo_cs_token':        'MAEYO_CS_TOKEN',
    'portone_api_secret':    'PORTONE_API_SECRET',
    'portone_store_id':      'PORTONE_STORE_ID',
    'portone_channel_card':  'PORTONE_CHANNEL_KEY_CARD',
    'portone_channel_kakao': 'PORTONE_CHANNEL_KEY_KAKAO',
    'google_tts_api_key':    'GOOGLE_TTS_API_KEY',
    'kling_access_key':      'KLING_ACCESS_KEY',
    'kling_secret_key':      'KLING_SECRET_KEY',
    'kling_base_url':        'KLING_BASE_URL',
    'image_provider':        'IMAGE_PROVIDER',
    'smtp_host':             'SMTP_HOST',
    'smtp_port':             'SMTP_PORT',
    'smtp_user':             'SMTP_USER',
    'smtp_password':         'SMTP_PASSWORD',
    'smtp_from':             'SMTP_FROM',
}


def get_config(key: str, _supabase=None) -> str:
    """설정값 조회 — 환경변수 우선, 없으면 saas_config DB.

    _supabase: Celery 워커 등 Flask 컨텍스트 없는 환경에서 직접 클라이언트 전달 가능.
    """
    env_key = _ENV_MAP.get(key, key.upper())
    val = os.environ.get(env_key, '')
    if val:
        return val

    # Supabase 클라이언트 획득 (직접 전달 → Flask current_app → 포기)
    supabase = _supabase
    if supabase is None:
        try:
            from flask import current_app
            supabase = current_app.supabase
        except Exception:
            pass

    if supabase is None:
        # 환경변수에서 URL/KEY로 직접 생성 (워커 폴백)
        try:
            sb_url = os.environ.get('SUPABASE_URL', '')
            sb_key = os.environ.get('SUPABASE_SERVICE_KEY', '')
            if sb_url and sb_key:
                from supabase import create_client
                supabase = create_client(sb_url, sb_key)
        except Exception:
            pass

    if supabase is None:
        return ''

    try:
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
