"""구독 / 포인트 충전 / 결제 관리 — 팀(Operator) 풀 지원.

포트원 v2 기반:
  - 빌링키 저장 / 삭제  (정기결제 등록)
  - 구독 결제 완료 콜백 (payment/complete)
  - PortOne 웹훅 수신  (webhook) — HMAC 서명 검증
  - 구독 자동갱신 해제  (cancel-subscription)
  - 환불 요청          (refund/request)  — 7일 이내 자동 / 이후 수동
  - 포인트 잔액 페이지  (points)

VAT 정책: amount(표시가) 에서 1/11 이 부가세, 나머지가 공급가.
던닝 정책: 결제 실패 1~2회 → past_due (3일 후 재시도), 3회 이상 → locked.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, current_app, jsonify)
from flask_login import login_required, current_user

from services.tz_utils import now_kst
from services.payment_service import _split_vat

logger = logging.getLogger(__name__)
billing_bp = Blueprint('billing', __name__)

# 포인트 충전 패키지
POINT_PACKAGES = [
    {'points': 10000, 'price': 24900, 'label': '10,000P', 'badge': '인기'},
    {'points': 22000, 'price': 49900, 'label': '22,000P', 'badge': '최저가/P'},
    {'points': 50000, 'price': 99000, 'label': '50,000P', 'badge': '대용량'},
]

PLAN_PRICES = {
    'growth':     {'label': 'Growth',     'price': 24900, 'points': 10000},
    'pro':        {'label': 'Pro',        'price': 49900, 'points': 22000},
    'enterprise': {'label': 'Enterprise', 'price': 99000, 'points': 50000},
}


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────

def _scoped_subscription_query(supabase):
    q = supabase.table('subscriptions').select('*')
    if current_user.operator_id:
        return q.eq('operator_id', current_user.operator_id)
    return q.is_('operator_id', 'null').eq('user_id', current_user.id)


def _scoped_payments_query(supabase):
    q = supabase.table('payments').select('*')
    if current_user.operator_id:
        return q.eq('operator_id', current_user.operator_id)
    return q.is_('operator_id', 'null').eq('user_id', current_user.id)


def _can_manage_subscription() -> bool:
    if not current_user.operator_id:
        return True
    return current_user.is_operator_admin


def _get_billing_owner() -> dict:
    """현재 사용자의 빌링 소유자 정보 반환.

    Returns:
        {'type': 'operator'|'user', 'id': str, 'billing_key': str|None, 'billing_key_pg': str|None}
    """
    supabase = current_app.supabase
    if current_user.operator_id:
        op = supabase.table('operators').select(
            'id, billing_key, billing_key_pg, card_info'
        ).eq('id', current_user.operator_id).limit(1).execute()
        data = (op.data or [{}])[0]
        return {
            'type': 'operator',
            'id': str(current_user.operator_id),
            'billing_key': data.get('billing_key'),
            'billing_key_pg': data.get('billing_key_pg'),
            'card_info': data.get('card_info'),
        }
    u = supabase.table('users').select(
        'id, billing_key, billing_key_pg, card_info'
    ).eq('id', current_user.id).limit(1).execute()
    data = (u.data or [{}])[0]
    return {
        'type': 'user',
        'id': str(current_user.id),
        'billing_key': data.get('billing_key'),
        'billing_key_pg': data.get('billing_key_pg'),
        'card_info': data.get('card_info'),
    }


def _record_payment(supabase, *, payment_id: str, user_id: str, operator_id: str | None,
                    payment_type: str, amount: int, plan_type: str = '',
                    points_granted: int = 0, method: str = '', pg_provider: str = '',
                    receipt_url: str = '', raw_data: dict | None = None) -> dict:
    """payments 테이블 행 삽입/갱신 (upsert by payment_id). VAT 자동 분리."""
    supply, tax = _split_vat(amount)
    now_str = now_kst().isoformat()
    row = {
        'id':              str(uuid.uuid4()),
        'user_id':         user_id,
        'payment_id':      payment_id,
        'payment_type':    payment_type,
        'plan_type':       plan_type or None,
        'points_granted':  points_granted,
        'amount':          amount,
        'supply_amount':   supply,
        'tax_amount':      tax,
        'method':          method or None,
        'pg_provider':     pg_provider or None,
        'receipt_url':     receipt_url or None,
        'raw_data':        raw_data,
        'status':          'paid',
        'paid_at':         now_str,
        'created_at':      now_str,
        'updated_at':      now_str,
    }
    if operator_id:
        row['operator_id'] = operator_id
    # UPSERT on payment_id (웹훅과 콜백 중복 방지)
    try:
        res = supabase.table('payments').upsert(row, on_conflict='payment_id').execute()
        return res.data[0] if res.data else row
    except Exception:
        # UNIQUE 제약이 없는 구버전 DB — insert fallback
        supabase.table('payments').insert(row).execute()
        return row


# ─────────────────────────────────────────────────────────────
# 웹훅 내부 핸들러
# ─────────────────────────────────────────────────────────────

def _webhook_handle_paid(supabase, data: dict):
    """Transaction.Paid — payments upsert + 구독 활성화 + 포인트 지급."""
    payment_id  = data.get('paymentId') or data.get('id', '')
    amount      = 0
    try:
        amount = int((data.get('amount') or {}).get('total') or 0)
    except Exception:
        pass
    pg_provider = (data.get('channel') or {}).get('pgProvider', '')
    receipt_url = data.get('receiptUrl', '')
    method_str  = ''
    try:
        card = (data.get('method') or {}).get('card') or {}
        method_str = f"{card.get('brand', '')} *{(card.get('number', '') or '')[-4:]}"
    except Exception:
        pass

    # payment_id 에서 소유자 역추적
    owner = _resolve_owner_from_payment_id(supabase, payment_id)
    if not owner:
        logger.warning(f'[Webhook] owner 매칭 실패: {payment_id}')
        return

    user_id     = owner.get('user_id') or ''
    operator_id = owner.get('operator_id')
    plan_type   = owner.get('plan_type', '')
    points      = PLAN_PRICES.get(plan_type, {}).get('points', 0) if plan_type else 0

    # payments upsert
    _record_payment(
        supabase,
        payment_id=payment_id,
        user_id=user_id,
        operator_id=operator_id,
        payment_type='subscription',
        amount=amount,
        plan_type=plan_type,
        points_granted=points,
        method=method_str,
        pg_provider=pg_provider,
        receipt_url=receipt_url,
        raw_data=data,
    )

    # 구독 활성화 + failed_attempt_count 리셋
    now_str    = now_kst().isoformat()
    period_end = (now_kst() + timedelta(days=30)).isoformat()
    sub_data   = {
        'status':               'active',
        'failed_attempt_count': 0,
        'last_retry_at':        None,
        'updated_at':           now_str,
    }
    # past_due/locked 상태면 operators/users 도 is_active=True 복구
    try:
        sub_q = supabase.table('subscriptions').select('status')
        sub_q = (sub_q.eq('operator_id', operator_id) if operator_id
                 else sub_q.is_('operator_id', 'null').eq('user_id', user_id))
        existing = (sub_q.limit(1).execute().data or [{}])[0]
        if existing.get('status') in ('past_due', 'cancelled'):
            if operator_id:
                supabase.table('operators').update({'is_active': True}).eq('id', operator_id).execute()
            else:
                supabase.table('users').update({'is_active': True}).eq('id', user_id).execute()
    except Exception as e:
        logger.warning(f'[Webhook] is_active 복구 실패: {e}')

    # 구독 기간 업데이트
    sub_data.update({
        'current_period_start': now_str,
        'current_period_end':   period_end,
        'next_billing_at':      period_end,
        'auto_renewal':         True,
    })
    if not plan_type:
        try:
            if operator_id:
                op_row = supabase.table('operators').select('plan_type').eq('id', operator_id).single().execute().data or {}
                sub_data['plan_type'] = op_row.get('plan_type', 'growth')
            else:
                u_row = supabase.table('users').select('plan_type').eq('id', user_id).single().execute().data or {}
                sub_data['plan_type'] = u_row.get('plan_type', 'growth')
        except Exception:
            pass

    try:
        upd = supabase.table('subscriptions')
        if operator_id:
            upd = upd.update(sub_data).eq('operator_id', operator_id)
        else:
            upd = upd.update(sub_data).is_('operator_id', 'null').eq('user_id', user_id)
        upd.execute()
    except Exception as e:
        logger.warning(f'[Webhook] subscriptions 업데이트 실패: {e}')

    # 구독 포인트 지급 (중복 방지: payment_id ref_id)
    if points > 0:
        try:
            from services.point_service import add_points, get_ledger
            # 이미 지급됐는지 확인
            if operator_id:
                dup_q = supabase.table('point_ledger').select('id').eq(
                    'ref_id', payment_id).eq('operator_id', operator_id).eq('type', 'subscription_grant').limit(1).execute()
            else:
                dup_q = supabase.table('point_ledger').select('id').is_(
                    'operator_id', 'null').eq('user_id', user_id).eq(
                    'ref_id', payment_id).eq('type', 'subscription_grant').limit(1).execute()
            if not (dup_q.data):
                from models import User as _User
                # proxy owner object
                class _OwnerProxy:
                    id = user_id
                    operator_id = operator_id
                add_points(_OwnerProxy(), points, 'subscription_grant',
                           ref_id=payment_id,
                           note=f'{PLAN_PRICES.get(plan_type, {}).get("label", plan_type)} 구독 포인트',
                           expires_at=period_end)
        except Exception as e:
            logger.warning(f'[Webhook] 포인트 지급 실패 (무시): {e}')

    logger.info(f'[Webhook] 결제 완료 처리: pid={payment_id} owner={user_id} amt={amount}')


def _webhook_handle_failed(supabase, data: dict):
    """Transaction.Failed — payments upsert + 던닝(실패 횟수 증가)."""
    payment_id = data.get('paymentId') or data.get('id', '')
    owner = _resolve_owner_from_payment_id(supabase, payment_id)
    if not owner:
        logger.warning(f'[Webhook/Failed] owner 매칭 실패: {payment_id}')
        return

    user_id     = owner.get('user_id') or ''
    operator_id = owner.get('operator_id')
    now_str     = now_kst().isoformat()

    # 실패 결제 기록
    try:
        row = {
            'id':           str(uuid.uuid4()),
            'user_id':      user_id,
            'payment_id':   payment_id,
            'payment_type': 'subscription',
            'amount':       0,
            'supply_amount': 0,
            'tax_amount':   0,
            'status':       'failed',
            'raw_data':     data,
            'created_at':   now_str,
            'updated_at':   now_str,
        }
        if operator_id:
            row['operator_id'] = operator_id
        supabase.table('payments').upsert(row, on_conflict='payment_id').execute()
    except Exception as e:
        logger.warning(f'[Webhook/Failed] 결제 실패 기록 오류: {e}')

    # 현재 실패 횟수 조회
    try:
        sub_q = supabase.table('subscriptions').select('failed_attempt_count')
        sub_q = (sub_q.eq('operator_id', operator_id) if operator_id
                 else sub_q.is_('operator_id', 'null').eq('user_id', user_id))
        sub_row = (sub_q.limit(1).execute().data or [{}])[0]
        fail_count = int(sub_row.get('failed_attempt_count') or 0) + 1
    except Exception:
        fail_count = 1

    next_retry = (now_kst() + timedelta(days=3)).isoformat()

    if fail_count >= 3:
        # 3회 이상 — 잠금
        sub_upd = {
            'status':               'cancelled',
            'failed_attempt_count': fail_count,
            'last_retry_at':        now_str,
            'updated_at':           now_str,
        }
        if operator_id:
            try:
                supabase.table('operators').update({'is_active': False}).eq('id', operator_id).execute()
            except Exception:
                pass
        else:
            try:
                supabase.table('users').update({'is_active': False}).eq('id', user_id).execute()
            except Exception:
                pass
        logger.warning(f'[Dunning] 3회 실패 잠금: user={user_id} op={operator_id}')
    else:
        # 1~2회 — past_due, 3일 후 재시도
        sub_upd = {
            'status':               'past_due',
            'failed_attempt_count': fail_count,
            'last_retry_at':        now_str,
            'next_billing_at':      next_retry,
            'updated_at':           now_str,
        }

    try:
        upd = supabase.table('subscriptions')
        if operator_id:
            upd = upd.update(sub_upd).eq('operator_id', operator_id)
        else:
            upd = upd.update(sub_upd).is_('operator_id', 'null').eq('user_id', user_id)
        upd.execute()
    except Exception as e:
        logger.error(f'[Webhook/Failed] subscriptions 업데이트 실패: {e}')


def _resolve_owner_from_payment_id(supabase, payment_id: str) -> dict | None:
    """payment_id 'sub_{user_or_op_id[:8]}_{uuid}' 에서 소유자를 역추적.

    Returns:
        {'user_id': str, 'operator_id': str|None, 'plan_type': str}
    """
    try:
        # DB에서 직접 조회 (가장 정확)
        row = supabase.table('payments').select(
            'user_id, operator_id, plan_type'
        ).eq('payment_id', payment_id).limit(1).execute().data
        if row:
            return row[0]
    except Exception:
        pass

    # payment_id prefix 추출 (fallback)
    try:
        parts = (payment_id or '').split('_')
        if len(parts) < 2:
            return None
        prefix = parts[1]

        # operator 매칭
        op_rows = supabase.table('operators').select('id, plan_type').ilike('id', f'{prefix}%').limit(2).execute().data or []
        if len(op_rows) == 1:
            return {'user_id': '', 'operator_id': op_rows[0]['id'], 'plan_type': op_rows[0].get('plan_type', '')}

        # user 매칭
        u_rows = supabase.table('users').select('id, plan_type').ilike('id', f'{prefix}%').limit(2).execute().data or []
        if len(u_rows) == 1:
            return {'user_id': u_rows[0]['id'], 'operator_id': None, 'plan_type': u_rows[0].get('plan_type', '')}
    except Exception as e:
        logger.warning(f'[Payment] owner 역추적 실패 pid={payment_id}: {e}')
    return None


# ─────────────────────────────────────────────────────────────
# 라우트 — 대시보드
# ─────────────────────────────────────────────────────────────

@billing_bp.route('/')
@login_required
def index():
    supabase = current_app.supabase
    from services.point_service import get_balance, get_ledger, get_expiry_summary
    balance        = get_balance(current_user)
    ledger         = get_ledger(current_user, limit=10)
    expiry_summary = get_expiry_summary(current_user)

    subscription = None
    days_left    = None
    try:
        sub = _scoped_subscription_query(supabase).order('created_at', desc=True).limit(1).execute()
        subscription = sub.data[0] if sub.data else None
        if subscription and subscription.get('current_period_end'):
            end_dt = datetime.fromisoformat(
                subscription['current_period_end'].replace('Z', '+00:00')
            )
            diff      = end_dt - datetime.now(timezone.utc)
            days_left = max(0, diff.days)
    except Exception as e:
        logger.debug(f'[BILLING] subscription 조회 실패: {e}')

    payments = []
    try:
        p = _scoped_payments_query(supabase).order('created_at', desc=True).limit(10).execute()
        payments = p.data or []
    except Exception as e:
        logger.debug(f'[BILLING] payments 조회 실패: {e}')

    owner_info = {}
    try:
        owner_info = _get_billing_owner()
    except Exception:
        pass

    from models import PLAN_FEATURES
    from services.payment_service import _get_config
    return render_template('billing/index.html',
                           balance=balance,
                           ledger=ledger,
                           expiry_summary=expiry_summary,
                           subscription=subscription,
                           days_left=days_left,
                           payments=payments,
                           owner_info=owner_info,
                           can_manage=_can_manage_subscription(),
                           is_team_mode=bool(current_user.operator_id),
                           PLAN_FEATURES=PLAN_FEATURES,
                           PLAN_PRICES=PLAN_PRICES,
                           POINT_PACKAGES=POINT_PACKAGES,
                           portone_store_id=_get_config('portone_store_id'),
                           portone_channel_card=_get_config('portone_channel_card'),
                           portone_channel_kakao=_get_config('portone_channel_kakao'),
                           is_superadmin=current_user.is_superadmin)


@billing_bp.route('/points')
@login_required
def points():
    from services.point_service import get_balance, get_ledger, get_expiry_summary
    balance        = get_balance(current_user)
    ledger         = get_ledger(current_user, limit=30)
    expiry_summary = get_expiry_summary(current_user)
    return render_template('billing/points.html',
                           balance=balance,
                           ledger=ledger,
                           expiry_summary=expiry_summary,
                           can_manage=_can_manage_subscription(),
                           is_team_mode=bool(current_user.operator_id),
                           POINT_PACKAGES=POINT_PACKAGES)


# ─────────────────────────────────────────────────────────────
# 빌링키 저장 / 삭제
# ─────────────────────────────────────────────────────────────

@billing_bp.route('/billing-key/save', methods=['POST'])
@login_required
def billing_key_save():
    """프론트엔드 PortOne JS SDK 에서 발급된 빌링키 저장.

    Body: {"billing_key": "billing-key-xxx...", "pg": "card"|"kakaopay"}
    """
    if not _can_manage_subscription():
        return jsonify(ok=False, message='관리자만 결제 수단을 등록할 수 있습니다.'), 403

    data = request.get_json(force=True) or {}
    billing_key = (data.get('billing_key') or '').strip()
    pg = (data.get('pg') or 'card').strip().lower()
    if pg not in ('card', 'kakaopay'):
        pg = 'card'
    if not billing_key:
        return jsonify(ok=False, message='빌링키가 없습니다.'), 400

    supabase = current_app.supabase

    # 카드 정보 조회
    card_info = {}
    try:
        from services.payment_service import get_billing_key_info, parse_card_info
        info = get_billing_key_info(billing_key)
        if info:
            card_info = parse_card_info(info, pg)
    except Exception as e:
        logger.warning(f'[BILLING] 빌링키 정보 조회 실패 (무시): {e}')

    now_str = now_kst().isoformat()
    bk_data = {
        'billing_key':    billing_key,
        'billing_key_pg': pg,
        'billing_key_at': now_str,
        'card_info':      card_info,
    }

    try:
        if current_user.operator_id:
            supabase.table('operators').update(bk_data).eq('id', current_user.operator_id).execute()
        else:
            supabase.table('users').update(bk_data).eq('id', current_user.id).execute()
        logger.info(f'[BILLING] 빌링키 저장: user={current_user.id} pg={pg}')
        return jsonify(ok=True, card_info=card_info)
    except Exception as e:
        logger.error(f'[BILLING] 빌링키 저장 실패: {e}')
        return jsonify(ok=False, message='저장 중 오류가 발생했습니다.'), 500


@billing_bp.route('/billing-key/delete', methods=['POST'])
@login_required
def billing_key_delete():
    """카드 등록 해제 — PortOne 측 빌링키도 함께 삭제."""
    if not _can_manage_subscription():
        return jsonify(ok=False, message='관리자만 결제 수단을 삭제할 수 있습니다.'), 403

    supabase = current_app.supabase
    owner = _get_billing_owner()
    billing_key = owner.get('billing_key')

    if billing_key:
        try:
            from services.payment_service import delete_billing_key
            delete_billing_key(billing_key, reason='user requested')
        except Exception as e:
            logger.warning(f'[BILLING] PortOne 빌링키 삭제 실패 (DB는 진행): {e}')

    try:
        bk_clear = {'billing_key': None, 'billing_key_pg': None, 'billing_key_at': None, 'card_info': None}
        if current_user.operator_id:
            supabase.table('operators').update(bk_clear).eq('id', current_user.operator_id).execute()
        else:
            supabase.table('users').update(bk_clear).eq('id', current_user.id).execute()
        return jsonify(ok=True)
    except Exception as e:
        logger.error(f'[BILLING] 빌링키 DB 삭제 실패: {e}')
        return jsonify(ok=False, message='처리 중 오류가 발생했습니다.'), 500


# ─────────────────────────────────────────────────────────────
# 빌링키 구독 등록 + 첫 달 청구 (프론트에서 호출)
# ─────────────────────────────────────────────────────────────

@billing_bp.route('/billing-key/subscribe', methods=['POST'])
@login_required
def billing_key_subscribe():
    """빌링키 발급 후 구독 등록 + 첫 달 즉시 청구.

    Body: {"billing_key": "billing-key-xxx", "pg": "card"|"kakaopay", "plan_type": "growth"|"pro"|"enterprise"}

    Flow:
      1. billing_key 저장 (users/operators)
      2. charge_subscription으로 첫 달 청구
      3. payments upsert + subscriptions insert + 포인트 지급
    """
    if not _can_manage_subscription():
        return jsonify(ok=False, message='팀 결제는 관리자만 진행할 수 있습니다.'), 403

    data        = request.get_json(force=True) or {}
    billing_key = (data.get('billing_key') or '').strip()
    pg          = (data.get('pg') or 'card').strip().lower()
    plan_type   = (data.get('plan_type') or '').strip()

    if not billing_key:
        return jsonify(ok=False, message='빌링키가 없습니다.'), 400
    if pg not in ('card', 'kakaopay'):
        pg = 'card'
    if plan_type not in PLAN_PRICES:
        return jsonify(ok=False, message='유효하지 않은 플랜입니다.'), 400

    supabase    = current_app.supabase
    operator_id = current_user.operator_id
    plan_info   = PLAN_PRICES[plan_type]
    amount      = plan_info['price']
    now_str     = now_kst().isoformat()

    # ── 1. 카드 정보 조회 & 빌링키 저장 ──
    card_info = {}
    try:
        from services.payment_service import get_billing_key_info, parse_card_info
        info = get_billing_key_info(billing_key)
        if info:
            card_info = parse_card_info(info, pg)
    except Exception as e:
        logger.warning(f'[Subscribe] 빌링키 정보 조회 실패 (무시): {e}')

    bk_data = {
        'billing_key':    billing_key,
        'billing_key_pg': pg,
        'billing_key_at': now_str,
        'card_info':      card_info,
    }
    try:
        if operator_id:
            supabase.table('operators').update(bk_data).eq('id', operator_id).execute()
        else:
            supabase.table('users').update(bk_data).eq('id', current_user.id).execute()
    except Exception as e:
        logger.error(f'[Subscribe] 빌링키 저장 실패: {e}')
        return jsonify(ok=False, message='카드 등록 중 오류가 발생했습니다.'), 500

    # ── 2. 첫 달 즉시 청구 ──
    from services.payment_service import charge_subscription
    owner_id   = operator_id or current_user.id
    order_name = f'매실 스튜디오 {plan_info["label"]} 플랜 구독'
    try:
        owner_row  = supabase.table('operators' if operator_id else 'users') \
            .select('email, name').eq('id', owner_id).single().execute().data or {}
    except Exception:
        owner_row = {}
    customer = {
        'customerId': owner_id,
        'fullName':   owner_row.get('name') or '',
        'email':      owner_row.get('email') or '',
    }

    result = charge_subscription(
        owner_id=owner_id,
        billing_key=billing_key,
        amount=amount,
        order_name=order_name,
        pg=pg,
        customer=customer,
        id_prefix='sub',
    )

    if not result.get('success'):
        # 빌링키 저장은 유지 (다음 시도 때 재사용 가능)
        return jsonify(ok=False, message=f'결제 실패: {result.get("error", "알 수 없는 오류")}'), 402

    payment_id = result['payment_id']
    tax        = round(amount / 11.0)
    supply     = amount - tax
    period_end = (now_kst() + timedelta(days=30)).isoformat()

    # ── 3. payments 기록 ──
    _record_payment(
        supabase,
        payment_id=payment_id,
        user_id=current_user.id,
        operator_id=operator_id,
        payment_type='subscription',
        amount=amount,
        plan_type=plan_type,
        points_granted=plan_info['points'],
        method=card_info.get('display') or pg,
        pg_provider=pg,
        receipt_url='',
        raw_data=result.get('data') or {},
    )

    # ── 4. plan_type 갱신 ──
    if operator_id:
        supabase.table('operators').update({
            'plan_type': plan_type, 'updated_at': now_str,
        }).eq('id', operator_id).execute()
    else:
        supabase.table('users').update({
            'plan_type': plan_type, 'updated_at': now_str,
        }).eq('id', current_user.id).execute()

    # ── 5. subscriptions upsert ──
    sub_row = {
        'user_id':              current_user.id,
        'plan_type':            plan_type,
        'status':               'active',
        'billing_key':          billing_key,
        'current_period_start': now_str,
        'current_period_end':   period_end,
        'next_billing_at':      period_end,
        'auto_renewal':         True,
        'failed_attempt_count': 0,
        'created_at':           now_str,
        'updated_at':           now_str,
    }
    if operator_id:
        sub_row['operator_id'] = operator_id
    try:
        supabase.table('subscriptions').insert(sub_row).execute()
    except Exception as e:
        logger.warning(f'[Subscribe] subscriptions insert 실패 (계속): {e}')

    # ── 6. 구독 포인트 지급 (중복 방지) ──
    from services.point_service import add_points, get_balance
    if operator_id:
        dup_q = supabase.table('point_ledger').select('id').eq(
            'ref_id', payment_id).eq('operator_id', operator_id).eq(
            'type', 'subscription_grant').limit(1).execute()
    else:
        dup_q = supabase.table('point_ledger').select('id').is_(
            'operator_id', 'null').eq('user_id', current_user.id).eq(
            'ref_id', payment_id).eq('type', 'subscription_grant').limit(1).execute()

    if not dup_q.data:
        new_balance = add_points(
            current_user, plan_info['points'], 'subscription_grant',
            ref_id=payment_id,
            note=f'{plan_info["label"]} 구독 포인트 지급',
            expires_at=period_end,
        )
    else:
        new_balance = get_balance(current_user)

    card_display = card_info.get('display') or ('카카오페이' if pg == 'kakaopay' else '카드')
    return jsonify(
        ok=True,
        new_balance=new_balance,
        card_display=card_display,
        message=f'{plan_info["label"]} 플랜 시작! 매달 {amount:,}원 자동 갱신됩니다.',
    )


# ─────────────────────────────────────────────────────────────
# PortOne 결제 완료 콜백 (프론트에서 호출)
# ─────────────────────────────────────────────────────────────

@billing_bp.route('/payment/complete', methods=['POST'])
@login_required
def payment_complete():
    """PortOne 결제 완료 콜백 — 프론트에서 payment_id 전달.

    팀 모드: operator_admin 만 결제 가능. 결과는 operator 풀에 반영.
    VAT: supply_amount + tax_amount = amount (1/11 부가세).
    """
    if not _can_manage_subscription():
        return jsonify(ok=False, message='팀 결제는 관리자만 진행할 수 있습니다.'), 403

    data = request.get_json() or {}
    payment_id   = data.get('payment_id', '')
    payment_type = data.get('payment_type', 'point_purchase')
    package_idx  = data.get('package_idx')
    plan_type    = data.get('plan_type', '')

    if not payment_id:
        return jsonify(ok=False, message='payment_id가 없습니다.'), 400

    supabase    = current_app.supabase
    operator_id = current_user.operator_id

    try:
        from services.payment_service import get_payment
        resp    = get_payment(payment_id)
        payment = resp.get('payment', resp)

        if payment.get('status') != 'PAID':
            return jsonify(ok=False, message='결제가 완료되지 않았습니다.'), 400

        amount      = payment['amount']['total']
        pg_provider = (payment.get('channel') or {}).get('pgProvider', '')
        receipt_url = payment.get('receiptUrl', '')
        method_str  = ''
        try:
            card = (payment.get('method') or {}).get('card') or {}
            method_str = f"{card.get('brand', '')} *{(card.get('number', '') or '')[-4:]}"
        except Exception:
            pass

        if payment_type == 'test_payment':
            if not current_user.is_superadmin:
                return jsonify(ok=False, message='권한 없음'), 403
            if amount != 100:
                return jsonify(ok=False, message='테스트 결제금액은 100원이어야 합니다.'), 400
            _record_payment(
                supabase,
                payment_id=payment_id,
                user_id=current_user.id,
                operator_id=operator_id,
                payment_type='test_payment',
                amount=100,
                plan_type='',
                points_granted=0,
                method=method_str,
                pg_provider=pg_provider,
                receipt_url=receipt_url,
                raw_data=payment,
            )
            return jsonify(ok=True, new_balance=None, message='테스트 결제 완료 (100원). 결제 내역에서 환불 테스트를 진행하세요.')

        if payment_type == 'point_purchase':
            pkg = POINT_PACKAGES[int(package_idx)]
            if amount != pkg['price']:
                return jsonify(ok=False, message='결제금액 불일치'), 400

            # 구독자 전용
            has_active_sub = False
            try:
                sub_q = _scoped_subscription_query(supabase).eq('status', 'active').limit(1).execute()
                has_active_sub = bool(sub_q.data)
            except Exception:
                pass
            if not has_active_sub:
                return jsonify(
                    ok=False,
                    message='포인트 충전은 구독 중인 회원만 이용할 수 있습니다.'
                ), 403

            # 결제 기록 (VAT 자동 분리)
            _record_payment(
                supabase,
                payment_id=payment_id,
                user_id=current_user.id,
                operator_id=operator_id,
                payment_type='point_purchase',
                amount=amount,
                points_granted=pkg['points'],
                method=method_str,
                pg_provider=pg_provider,
                receipt_url=receipt_url,
                raw_data=payment,
            )

            # 구매 포인트 지급 (365일 만료)
            from services.point_service import add_points
            purchase_expiry = (now_kst() + timedelta(days=365)).isoformat()
            new_balance = add_points(
                current_user, pkg['points'], 'purchase',
                ref_id=payment_id,
                note=f'포인트 충전 {pkg["label"]}',
                expires_at=purchase_expiry,
            )
            return jsonify(ok=True, new_balance=new_balance, message=f'{pkg["label"]} 충전 완료!')

        elif payment_type == 'subscription':
            if plan_type not in PLAN_PRICES:
                return jsonify(ok=False, message='유효하지 않은 플랜'), 400

            plan_info = PLAN_PRICES[plan_type]
            if amount != plan_info['price']:
                return jsonify(ok=False, message='결제금액 불일치'), 400

            # 결제 기록
            _record_payment(
                supabase,
                payment_id=payment_id,
                user_id=current_user.id,
                operator_id=operator_id,
                payment_type='subscription',
                amount=amount,
                plan_type=plan_type,
                points_granted=plan_info['points'],
                method=method_str,
                pg_provider=pg_provider,
                receipt_url=receipt_url,
                raw_data=payment,
            )

            # 플랜 변경
            if operator_id:
                supabase.table('operators').update({
                    'plan_type': plan_type, 'updated_at': now_kst().isoformat(),
                }).eq('id', operator_id).execute()
            else:
                supabase.table('users').update({
                    'plan_type': plan_type, 'updated_at': now_kst().isoformat(),
                }).eq('id', current_user.id).execute()

            # 구독 행 생성/갱신
            period_end = (now_kst() + timedelta(days=30)).isoformat()
            now_str    = now_kst().isoformat()
            sub_row = {
                'user_id':              current_user.id,
                'plan_type':            plan_type,
                'status':               'active',
                'current_period_start': now_str,
                'current_period_end':   period_end,
                'next_billing_at':      period_end,
                'auto_renewal':         True,
                'failed_attempt_count': 0,
                'created_at':           now_str,
                'updated_at':           now_str,
            }
            if operator_id:
                sub_row['operator_id'] = operator_id
            supabase.table('subscriptions').insert(sub_row).execute()

            # 구독 포인트 지급 (period_end 만료) — 중복 방지: webhook과 동일한 dedup guard
            from services.point_service import add_points
            if operator_id:
                dup_q = supabase.table('point_ledger').select('id').eq(
                    'ref_id', payment_id).eq('operator_id', operator_id).eq(
                    'type', 'subscription_grant').limit(1).execute()
            else:
                dup_q = supabase.table('point_ledger').select('id').is_(
                    'operator_id', 'null').eq('user_id', current_user.id).eq(
                    'ref_id', payment_id).eq('type', 'subscription_grant').limit(1).execute()
            if not dup_q.data:
                new_balance = add_points(
                    current_user, plan_info['points'], 'subscription_grant',
                    ref_id=payment_id,
                    note=f'{plan_info["label"]} 구독 포인트 지급',
                    expires_at=period_end,
                )
            else:
                from services.point_service import get_balance
                new_balance = get_balance(current_user)
            return jsonify(ok=True, new_balance=new_balance, message=f'{plan_info["label"]} 플랜 시작!')

    except Exception as e:
        import traceback
        logger.error(f'[BILLING] payment_complete error: {e}\n{traceback.format_exc()}')
        return jsonify(ok=False, message=f'결제 처리 중 오류: {str(e)}'), 500

    return jsonify(ok=False, message='알 수 없는 오류'), 500


# ─────────────────────────────────────────────────────────────
# PortOne 웹훅 (서버→서버, HMAC 검증)
# ─────────────────────────────────────────────────────────────

@billing_bp.route('/webhook', methods=['POST'])
def payment_webhook():
    """포트원 v2 웹훅 수신 — 결제 완료/실패 처리.

    SECURITY: webhook-signature HMAC 검증 (Standard Webhooks 표준).
    secret 미설정 · 헤더 누락 · 서명 불일치 · 5분 초과 → 401.
    CSRF 면제: 포트원 서버가 직접 POST, 브라우저 요청이 아님.
    """
    from services.payment_service import verify_webhook
    supabase  = current_app.supabase
    raw_body  = request.get_data() or b''
    headers_d = {k: v for k, v in request.headers.items()}

    if not verify_webhook(raw_body, headers_d):
        logger.warning(f'[Webhook] 서명 검증 실패 ip={request.remote_addr}')
        return jsonify(status='unauthorized'), 401

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify(status='bad_payload'), 400

    tx_type = payload.get('type', '')
    data    = payload.get('data', {}) or {}
    logger.info(f'[Webhook] 수신: type={tx_type}')

    if tx_type == 'Transaction.Paid':
        _webhook_handle_paid(supabase, data)
    elif tx_type == 'Transaction.Failed':
        _webhook_handle_failed(supabase, data)
    elif tx_type == 'BillingKey.Issued':
        logger.info(f'[Webhook] 빌링키 발급: billing_key={data.get("billingKey", "")[:20]}')

    return jsonify(status='ok')


# ─────────────────────────────────────────────────────────────
# 구독 취소 (자동갱신 해제)
# ─────────────────────────────────────────────────────────────

@billing_bp.route('/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    if not _can_manage_subscription():
        flash('팀 구독 해제는 관리자만 진행할 수 있습니다.', 'warning')
        return redirect(url_for('billing.index'))

    supabase = current_app.supabase
    try:
        upd = {
            'auto_renewal':  False,
            'cancelled_at':  now_kst().isoformat(),
            'updated_at':    now_kst().isoformat(),
        }
        if current_user.operator_id:
            (supabase.table('subscriptions').update(upd)
             .eq('operator_id', current_user.operator_id)
             .eq('status', 'active').execute())
        else:
            (supabase.table('subscriptions').update(upd)
             .is_('operator_id', 'null')
             .eq('user_id', current_user.id)
             .eq('status', 'active').execute())
        flash('구독이 취소되었습니다. 현재 구독 기간 만료일까지 계속 이용할 수 있으며, 이후 자동 청구가 중단됩니다.', 'info')
    except Exception as e:
        logger.error(f'[BILLING] cancel error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('billing.index'))


# ─────────────────────────────────────────────────────────────
# 환불 요청
# ─────────────────────────────────────────────────────────────

@billing_bp.route('/refund/request', methods=['POST'])
@login_required
def refund_request():
    """환불 요청 — 정책 기반 자동/수동 분기.

    자동 환불 (즉시 PortOne cancel API 호출):
      - 결제일로부터 7일 이내 + 미환불 상태 + payment_id 명시
      - 더블 클릭 race 방지: refund_status NULL→'processing' atomic lock

    수동 검토 (영업일 5일 이내):
      - 7일 초과 / payment_id 누락 / 이미 환불됨
      - refund_status = 'requested' 로 기록 → 어드민 UI에서 승인

    Body: {"payment_id": "sub_...", "reason": "사유", "amount": 0(전액)}
    """
    from services.payment_service import cancel_payment as po_cancel

    data       = request.get_json(force=True) or {}
    payment_id = (data.get('payment_id') or '').strip()
    reason     = (data.get('reason') or '').strip()[:500]
    req_amount = int(data.get('amount') or 0)

    if not reason:
        return jsonify(ok=False, message='환불 사유를 입력해주세요.'), 400

    supabase = current_app.supabase
    now_utc  = datetime.now(timezone.utc)

    # 소유자 필터
    if current_user.operator_id:
        pay_filter = lambda q: q.eq('operator_id', current_user.operator_id)
    else:
        pay_filter = lambda q: q.is_('operator_id', 'null').eq('user_id', current_user.id)

    # 결제 레코드 조회
    auto_ok  = False
    pay_row  = None
    if payment_id:
        try:
            q   = pay_filter(supabase.table('payments').select(
                'payment_id, amount, paid_at, status, refund_status'
            ).eq('payment_id', payment_id))
            pay_row = (q.limit(1).execute().data or [None])[0]
        except Exception as e:
            logger.warning(f'[Refund] 결제 조회 실패: {e}')

        if pay_row and pay_row.get('status') == 'paid' and not pay_row.get('refund_status'):
            try:
                from dateutil.parser import parse as _parse
                paid_dt = _parse(pay_row['paid_at'])
                if (now_utc - paid_dt).days <= 7:
                    auto_ok = True
            except Exception:
                pass

    # ── 자동 환불 (7일 이내) ──
    if auto_ok and pay_row:
        original = int(pay_row.get('amount') or 0)
        cancel_amt = req_amount if (0 < req_amount < original) else None

        # Atomic lock: refund_status NULL → 'processing' (race 방지)
        try:
            lock = (pay_filter(supabase.table('payments').update({
                'refund_status':        'processing',
                'refund_requested_at':  now_utc.isoformat(),
                'updated_at':           now_utc.isoformat(),
            }).eq('payment_id', payment_id)).is_('refund_status', 'null')).execute()
            if not lock.data:
                return jsonify(ok=False, message='이미 환불 처리가 진행 중입니다.'), 409
        except Exception as e:
            logger.warning(f'[Refund] lock 실패 (계속): {e}')

        result = {'success': False, 'error': 'API 오류'}
        try:
            result = po_cancel(payment_id, reason, amount=cancel_amt)
        except Exception as e:
            result = {'success': False, 'error': str(e)}

        if result.get('success'):
            refunded = int(result.get('cancelled_amount') or original)
            try:
                supabase.table('payments').update({
                    'refund_status':       'completed',
                    'refund_reason':       reason,
                    'refund_amount':       refunded,
                    'refund_payment_id':   result.get('cancellation_id'),
                    'refunded_at':         now_utc.isoformat(),
                    'updated_at':          now_utc.isoformat(),
                }).eq('payment_id', payment_id).execute()
            except Exception as e:
                logger.error(f'[Refund] DB 업데이트 실패 (PortOne 환불은 성공): {e}')
            logger.info(f'[Refund] 자동 환불 성공: pid={payment_id} amt={refunded}')
            return jsonify(ok=True, auto=True, refund_amount=refunded,
                           message=f'{refunded:,}원이 환불되었습니다. 결제 수단에 따라 영업일 1~3일 내 처리됩니다.')

        # 자동 실패 → 수동 회부
        logger.warning(f'[Refund] 자동 환불 실패 → 수동: {result.get("error")}')
        try:
            supabase.table('payments').update({
                'refund_status':  'requested',
                'refund_reason':  f'[자동실패: {result.get("error","")[:80]}] {reason}',
                'refund_amount':  cancel_amt or original,
                'refund_requested_at': now_utc.isoformat(),
                'updated_at':     now_utc.isoformat(),
            }).eq('payment_id', payment_id).execute()
        except Exception:
            pass
        return jsonify(ok=True, auto=False,
                       message='환불 요청이 접수되었습니다. 자동 처리 오류로 영업일 5일 이내 수동 검토 후 처리됩니다.')

    # ── 수동 검토 (7일 초과 / payment_id 누락) ──
    try:
        if payment_id and pay_row:
            supabase.table('payments').update({
                'refund_status':       'requested',
                'refund_reason':       reason,
                'refund_amount':       req_amount or None,
                'refund_requested_at': now_utc.isoformat(),
                'updated_at':          now_utc.isoformat(),
            }).eq('payment_id', payment_id).execute()
    except Exception as e:
        logger.warning(f'[Refund] 수동 접수 기록 실패: {e}')

    return jsonify(ok=True, auto=False,
                   message='환불 요청이 접수되었습니다. 영업일 5일 이내 처리 후 이메일로 안내됩니다.')
