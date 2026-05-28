"""PortOne v2 결제 서비스 — 매실인사이트 수준으로 업그레이드

빌링키 발급 / 정기결제 / 결제 조회 / 환불 / 웹훅 검증

PortOne v2 서버사이드: Authorization: PortOne {api_secret} 직접 사용.
(액세스 토큰 교환 방식은 JWT permissions 필터링으로 BILLING_KEY 권한 누락 문제 발생)
"""
import logging
import os
import uuid

import requests

logger = logging.getLogger(__name__)

PORTONE_BASE = 'https://api.portone.io'

# 부가세 정책: 표시가 = 공급가 + 부가세(10%).
# amount(=표시가)에서 1/11 이 부가세.
_VAT_DIVISOR = 11.0


def _split_vat(amount: int) -> tuple[int, int]:
    """결제 총액에서 (공급가, 부가세) 분리. amount 가 0/음수면 (0, 0)."""
    if amount <= 0:
        return 0, 0
    tax = round(amount / _VAT_DIVISOR)
    return amount - tax, tax


def _get_config(key: str) -> str:
    """saas_config 테이블 또는 환경변수에서 설정 읽기"""
    # 환경변수 우선
    env_map = {
        'portone_api_secret':    'PORTONE_API_SECRET',
        'portone_channel_card':  'PORTONE_CHANNEL_KEY_CARD',
        'portone_channel_kakao': 'PORTONE_CHANNEL_KEY_KAKAO',
        'portone_store_id':      'PORTONE_STORE_ID',
        'portone_webhook_secret': 'PORTONE_WEBHOOK_SECRET',
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
    """PortOne v2 서버사이드 인증 — API Secret 직접 사용."""
    return {
        'Authorization': f'PortOne {_get_config("portone_api_secret").strip()}',
        'Content-Type': 'application/json',
    }


# ──────────────────────────────────────
# 결제 조회
# ──────────────────────────────────────

def get_payment(payment_id: str) -> dict:
    """결제 정보 조회"""
    resp = requests.get(
        f'{PORTONE_BASE}/payments/{payment_id}',
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────
# 빌링키 조회
# ──────────────────────────────────────

def get_billing_key_info(billing_key: str) -> dict | None:
    """발급된 빌링키 정보 조회 (카드 마스킹번호 등)."""
    api_secret = _get_config('portone_api_secret')
    if not api_secret:
        return None
    try:
        r = requests.get(
            f'{PORTONE_BASE}/billing-keys/{billing_key}',
            headers=_headers(),
            timeout=8,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f'[PortOne] 빌링키 조회 실패: {e}')
        return None


# ──────────────────────────────────────
# 빌링키 삭제
# ──────────────────────────────────────

def delete_billing_key(billing_key: str, reason: str = 'subscription cancelled') -> bool:
    """포트원 빌링키 삭제 — 구독 완전 종료 시 호출."""
    try:
        r = requests.delete(
            f'{PORTONE_BASE}/billing-keys/{billing_key}',
            headers=_headers(),
            json={'reason': reason[:200]},
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        logger.error(f'[PortOne] 빌링키 삭제 실패: {e}')
        return False


# ──────────────────────────────────────
# 정기결제 (빌링키로 즉시 청구)
# ──────────────────────────────────────

def charge_subscription(
    owner_id: str,
    billing_key: str,
    amount: int,
    order_name: str,
    pg: str = 'card',
    customer: dict | None = None,
    id_prefix: str = 'sub',
) -> dict:
    """빌링키로 구독 결제 청구.

    Args:
        owner_id: user_id 또는 operator_id (앞 8자리만 payment_id 에 삽입)
        billing_key: PortOne 빌링키
        amount: 결제금액 (VAT 포함 표시가)
        order_name: 주문명 (예: 'Growth 플랜 구독')
        pg: 'card' | 'kakaopay'
        customer: 고객 정보 dict (선택)
        id_prefix: payment_id 앞 부분 (기본 'sub')

    Returns:
        {'success': bool, 'payment_id': str, 'error': str, 'data': dict}
    """
    api_secret = _get_config('portone_api_secret')
    if not api_secret:
        return {'success': False, 'error': '포트원 API 설정 필요'}

    channel_key = (
        _get_config('portone_channel_kakao')
        if pg == 'kakaopay'
        else _get_config('portone_channel_card')
    )
    if not channel_key:
        return {'success': False, 'error': f'채널키 미설정 (pg={pg})'}

    payment_id = f'{id_prefix}_{owner_id[:8]}_{uuid.uuid4().hex[:8]}'
    store_id = _get_config('portone_store_id')

    payload = {
        'storeId':    store_id,
        'channelKey': channel_key,
        'billingKey': billing_key,
        'orderName':  order_name,
        'amount':     {'total': amount},
        'currency':   'KRW',
        'customer':   customer or {},
    }

    logger.info(f'[PortOne] 정기결제 요청 owner={owner_id[:8]} amount={amount}')
    try:
        r = requests.post(
            f'{PORTONE_BASE}/payments/{payment_id}/billing-key',
            headers=_headers(),
            json=payload,
            timeout=15,
        )
        data = r.json()
        payment = data.get('payment', {})
        paid = r.status_code == 200 and (
            payment.get('status') == 'PAID' or payment.get('paidAt')
        )
        if paid:
            logger.info(f'[PortOne] 정기결제 성공: payment_id={payment_id}')
            return {'success': True, 'payment_id': payment_id, 'data': data}
        else:
            err = (
                payment.get('message')
                or (payment.get('failureReason') or {}).get('message')
                or data.get('message')
                or str(r.status_code)
            )
            logger.error(f'[PortOne] 정기결제 실패 owner={owner_id[:8]}: {err}')
            return {'success': False, 'payment_id': payment_id, 'error': err, 'data': data}
    except Exception as e:
        logger.error(f'[PortOne] 정기결제 예외 owner={owner_id[:8]}: {e}')
        return {'success': False, 'error': str(e)}


# ──────────────────────────────────────
# 결제 취소/환불
# ──────────────────────────────────────

def cancel_payment(payment_id: str, reason: str, amount: int | None = None) -> dict:
    """결제 취소(환불).

    Returns:
        {'success': bool, 'cancellation_id': str, 'cancelled_amount': int, 'error': str}
    """
    payload: dict = {'reason': reason[:200]}
    if amount and amount > 0:
        payload['amount'] = amount

    try:
        r = requests.post(
            f'{PORTONE_BASE}/payments/{payment_id}/cancel',
            headers=_headers(),
            json=payload,
            timeout=15,
        )
        data = r.json() if r.content else {}
        if r.status_code == 200:
            cancellation = data.get('cancellation') or {}
            return {
                'success': True,
                'cancellation_id': cancellation.get('id', ''),
                'cancelled_amount': cancellation.get('totalAmount', amount or 0),
                'data': data,
            }
        err = data.get('message') or data.get('type') or f'status={r.status_code}'
        return {'success': False, 'error': err, 'data': data}
    except Exception as e:
        logger.error(f'[PortOne] 취소 실패: {e}')
        return {'success': False, 'error': str(e)}


# ──────────────────────────────────────
# 빌링키 발급 (수동 카드입력, 드물게 사용)
# ──────────────────────────────────────

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


# ──────────────────────────────────────
# 웹훅 서명 검증 (PortOne v2 / Standard Webhooks)
# ──────────────────────────────────────

def verify_webhook(payload_bytes: bytes, headers: dict) -> bool:
    """포트원 v2 웹훅 서명 검증 (Standard Webhooks 표준).

    헤더:
      webhook-id        : 메시지 고유 ID
      webhook-timestamp : 발송 시각 (Unix epoch sec)
      webhook-signature : "v1,<base64-hmac-sha256>" (공백 구분 다중 가능)

    검증 로직:
      sigPayload = f"{webhook-id}.{webhook-timestamp}.{payload}"
      expected   = base64( hmac_sha256(secret, sigPayload) )

    Returns:
        bool — 검증 통과 여부. 시크릿 미설정 시 False (fail-closed).
    """
    import base64
    import hashlib
    import hmac as _hmac
    import time as _time

    secret = _get_config('portone_webhook_secret').strip()
    if not secret:
        logger.error('[PortOne] webhook_secret 미설정 — 검증 거부')
        return False

    # PortOne 시크릿은 'whsec_' 프리픽스 — base64 디코딩 후 raw bytes 로 사용
    if secret.startswith('whsec_'):
        try:
            secret_bytes = base64.b64decode(secret[6:])
        except Exception:
            logger.error('[PortOne] webhook_secret base64 디코딩 실패')
            return False
    else:
        secret_bytes = secret.encode()

    msg_id  = headers.get('webhook-id')  or headers.get('Webhook-Id')  or ''
    msg_ts  = headers.get('webhook-timestamp') or headers.get('Webhook-Timestamp') or ''
    msg_sig = headers.get('webhook-signature') or headers.get('Webhook-Signature') or ''

    if not (msg_id and msg_ts and msg_sig):
        logger.warning('[PortOne] 웹훅 서명 헤더 누락')
        return False

    # 타임스탬프 신선도 검증 (5분 허용)
    try:
        ts_int = int(msg_ts)
        now_ts = int(_time.time())
        if abs(now_ts - ts_int) > 300:
            logger.warning(f'[PortOne] 웹훅 타임스탬프 만료: now={now_ts} msg={ts_int}')
            return False
    except (TypeError, ValueError):
        return False

    # 서명 계산
    sig_payload = f'{msg_id}.{msg_ts}.{payload_bytes.decode("utf-8", errors="replace")}'
    expected = base64.b64encode(
        _hmac.new(secret_bytes, sig_payload.encode(), hashlib.sha256).digest()
    ).decode()

    # webhook-signature 헤더는 "v1,<sig> v1,<sig2>" 형식 가능 — 하나라도 매치되면 OK
    for part in msg_sig.split():
        if ',' not in part:
            continue
        ver, sig = part.split(',', 1)
        if ver == 'v1' and _hmac.compare_digest(sig, expected):
            return True

    logger.warning('[PortOne] 웹훅 서명 불일치')
    return False


# ──────────────────────────────────────
# 빌링키 카드 정보 파싱 헬퍼
# ──────────────────────────────────────

def parse_card_info(billing_key_info: dict, pg: str = 'card') -> dict:
    """PortOne 빌링키 조회 응답에서 카드 표시 정보 추출."""
    if pg == 'kakaopay':
        return {'pg': 'kakaopay', 'label': '카카오페이'}
    try:
        methods = billing_key_info.get('methods') or []
        if methods:
            card = (methods[0] or {}).get('card') or {}
            return {
                'pg': 'card',
                'brand': card.get('brand', ''),
                'last4': (card.get('number', '') or '')[-4:],
                'expiry': f"{card.get('expiryYear', '')}/{card.get('expiryMonth', '')}",
            }
    except Exception:
        pass
    return {'pg': 'card', 'label': '카드'}
