"""PortOne v2 결제 서비스"""
import os
import logging
import requests
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)

PORTONE_BASE = 'https://api.portone.io'


def _get_config(key: str) -> str:
    """saas_config 테이블 또는 환경변수에서 설정 읽기"""
    # 환경변수 우선
    env_map = {
        'portone_api_secret': 'PORTONE_API_SECRET',
        'portone_channel_card': 'PORTONE_CHANNEL_KEY_CARD',
        'portone_channel_kakao': 'PORTONE_CHANNEL_KEY_KAKAO',
        'portone_store_id': 'PORTONE_STORE_ID',
    }
    env_val = os.environ.get(env_map.get(key, key.upper()), '')
    if env_val:
        return env_val

    # DB fallback
    try:
        from flask import current_app
        supabase = current_app.supabase
        if supabase:
            row = supabase.table('saas_config').select('value_text, value_secret').eq('key', key).execute()
            if row.data:
                if row.data[0].get('value_secret'):
                    from services.crypto import decrypt_value
                    return decrypt_value(row.data[0]['value_secret'])
                return row.data[0].get('value_text', '')
    except Exception:
        pass
    return ''


def _headers() -> dict:
    # PortOne v2: api_secret 직접 사용 (JWT 토큰은 BILLING_KEY 권한 누락)
    return {
        'Authorization': f'PortOne {_get_config("portone_api_secret")}',
        'Content-Type': 'application/json',
    }


def get_payment(payment_id: str) -> dict:
    """결제 정보 조회"""
    resp = requests.get(
        f'{PORTONE_BASE}/payments/{payment_id}',
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def cancel_payment(payment_id: str, reason: str, amount: int = None) -> dict:
    """결제 취소/환불"""
    payload = {'reason': reason}
    if amount is not None:
        payload['amount'] = amount
    resp = requests.post(
        f'{PORTONE_BASE}/payments/{payment_id}/cancel',
        headers=_headers(),
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def issue_billing_key(customer_uid: str, card_number: str, expiry: str, birth: str, pwd_2digit: str) -> str:
    """빌링키 발급 (정기결제용)"""
    resp = requests.post(
        f'{PORTONE_BASE}/billing-keys',
        headers=_headers(),
        json={
            'channelKey': _get_config('portone_channel_card'),
            'customer': {'id': customer_uid},
            'method': {
                'card': {
                    'credential': {
                        'number': card_number,
                        'expiryYear': expiry[:2],
                        'expiryMonth': expiry[2:],
                        'birthOrBusinessRegistrationNumber': birth,
                        'passwordTwoDigits': pwd_2digit,
                    }
                }
            },
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()['billingKeyInfo']['billingKey']


def charge_with_billing_key(billing_key: str, payment_id: str, amount: int, name: str) -> dict:
    """빌링키로 즉시 결제"""
    resp = requests.post(
        f'{PORTONE_BASE}/payments/{payment_id}/billing-key',
        headers=_headers(),
        json={
            'billingKey': billing_key,
            'orderName': name,
            'amount': {'total': amount},
            'currency': 'KRW',
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
