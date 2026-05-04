"""구독 / 포인트 충전 / 결제 관리"""
import logging
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)
billing_bp = Blueprint('billing', __name__)

# 포인트 충전 패키지
POINT_PACKAGES = [
    {'points': 1000,  'price': 9900,  'label': '1,000P'},
    {'points': 3000,  'price': 24900, 'label': '3,000P', 'badge': '인기'},
    {'points': 10000, 'price': 69900, 'label': '10,000P', 'badge': '최저가/P'},
]

PLAN_PRICES = {
    'starter': {'label': 'Starter', 'price': 9900,  'points': 3000},
    'growth':  {'label': 'Growth',  'price': 24900, 'points': 10000},
    'pro':     {'label': 'Pro',     'price': 49900, 'points': 25000},
}


@billing_bp.route('/')
@login_required
def index():
    supabase = current_app.supabase
    from services.point_service import get_balance, get_ledger
    balance = get_balance(current_user.id)
    ledger = get_ledger(current_user.id, limit=10)

    # 구독 정보
    subscription = None
    days_left = None
    try:
        sub = supabase.table('subscriptions').select('*').eq(
            'user_id', current_user.id
        ).order('created_at', desc=True).limit(1).execute()
        subscription = sub.data[0] if sub.data else None
        if subscription and subscription.get('current_period_end'):
            from datetime import datetime, timezone
            end_str = subscription['current_period_end']
            end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
            diff = end_dt - datetime.now(timezone.utc)
            days_left = max(0, diff.days)
    except Exception:
        pass

    # 결제 내역
    payments = []
    try:
        p = supabase.table('payments').select('*').eq(
            'user_id', current_user.id
        ).order('created_at', desc=True).limit(10).execute()
        payments = p.data or []
    except Exception:
        pass

    from models import PLAN_FEATURES
    from services.payment_service import _get_config
    return render_template('billing/index.html',
                           balance=balance,
                           ledger=ledger,
                           subscription=subscription,
                           days_left=days_left,
                           payments=payments,
                           PLAN_FEATURES=PLAN_FEATURES,
                           PLAN_PRICES=PLAN_PRICES,
                           POINT_PACKAGES=POINT_PACKAGES,
                           portone_store_id=_get_config('portone_store_id'),
                           portone_channel_card=_get_config('portone_channel_card'))


@billing_bp.route('/points')
@login_required
def points():
    from services.point_service import get_balance, get_ledger
    balance = get_balance(current_user.id)
    ledger = get_ledger(current_user.id, limit=30)
    return render_template('billing/points.html',
                           balance=balance,
                           ledger=ledger,
                           POINT_PACKAGES=POINT_PACKAGES)


# ── PortOne 결제 완료 후 검증 웹훅/콜백 ──
@billing_bp.route('/payment/complete', methods=['POST'])
@login_required
def payment_complete():
    """PortOne 결제 완료 콜백 — 프론트에서 payment_id 전달"""
    data = request.get_json() or {}
    payment_id = data.get('payment_id', '')
    payment_type = data.get('payment_type', 'point_purchase')  # 'point_purchase' | 'subscription'
    package_idx = data.get('package_idx')
    plan_type = data.get('plan_type', '')

    if not payment_id:
        return jsonify(ok=False, message='payment_id가 없습니다.'), 400

    supabase = current_app.supabase
    try:
        from services.payment_service import get_payment
        payment = get_payment(payment_id)

        if payment.get('status') != 'PAID':
            return jsonify(ok=False, message='결제가 완료되지 않았습니다.'), 400

        amount = payment['amount']['total']

        if payment_type == 'point_purchase':
            pkg = POINT_PACKAGES[int(package_idx)]
            if amount != pkg['price']:
                return jsonify(ok=False, message='결제금액 불일치'), 400

            # 결제 기록
            supabase.table('payments').insert({
                'id': str(uuid.uuid4()),
                'user_id': current_user.id,
                'payment_id': payment_id,
                'payment_type': 'point_purchase',
                'points_granted': pkg['points'],
                'amount': amount,
                'status': 'paid',
                'paid_at': now_kst().isoformat(),
                'created_at': now_kst().isoformat(),
            }).execute()

            # 포인트 충전
            from services.point_service import add_points
            new_balance = add_points(
                current_user.id, pkg['points'], 'purchase',
                ref_id=payment_id,
                note=f'포인트 충전 {pkg["label"]}',
            )
            return jsonify(ok=True, new_balance=new_balance, message=f'{pkg["label"]} 충전 완료!')

        elif payment_type == 'subscription':
            if plan_type not in PLAN_PRICES:
                return jsonify(ok=False, message='유효하지 않은 플랜'), 400

            plan_info = PLAN_PRICES[plan_type]
            if amount != plan_info['price']:
                return jsonify(ok=False, message='결제금액 불일치'), 400

            # 결제 기록
            supabase.table('payments').insert({
                'id': str(uuid.uuid4()),
                'user_id': current_user.id,
                'payment_id': payment_id,
                'payment_type': 'subscription',
                'plan_type': plan_type,
                'points_granted': plan_info['points'],
                'amount': amount,
                'status': 'paid',
                'paid_at': now_kst().isoformat(),
                'created_at': now_kst().isoformat(),
            }).execute()

            # 플랜 변경
            supabase.table('users').update({
                'plan_type': plan_type,
                'updated_at': now_kst().isoformat(),
            }).eq('id', current_user.id).execute()

            # 구독 기록
            from datetime import timedelta
            period_end = (now_kst() + timedelta(days=30)).isoformat()
            supabase.table('subscriptions').insert({
                'user_id': current_user.id,
                'plan_type': plan_type,
                'status': 'active',
                'current_period_start': now_kst().isoformat(),
                'current_period_end': period_end,
                'next_billing_at': period_end,
                'auto_renewal': True,
                'created_at': now_kst().isoformat(),
                'updated_at': now_kst().isoformat(),
            }).execute()

            # 구독 포인트 지급
            from services.point_service import add_points
            new_balance = add_points(
                current_user.id, plan_info['points'], 'subscription_grant',
                ref_id=payment_id,
                note=f'{plan_info["label"]} 구독 포인트 지급',
            )
            return jsonify(ok=True, new_balance=new_balance, message=f'{plan_info["label"]} 플랜 시작!')

    except Exception as e:
        logger.error(f'[BILLING] payment_complete error: {e}')
        return jsonify(ok=False, message='결제 처리 중 오류가 발생했습니다.'), 500

    return jsonify(ok=False, message='알 수 없는 오류'), 500


@billing_bp.route('/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    supabase = current_app.supabase
    try:
        supabase.table('subscriptions').update({
            'auto_renewal': False,
            'cancelled_at': now_kst().isoformat(),
            'updated_at': now_kst().isoformat(),
        }).eq('user_id', current_user.id).eq('status', 'active').execute()
        flash('구독 자동갱신이 해제되었습니다. 현재 구독 기간 만료 후 무료 플랜으로 전환됩니다.', 'info')
    except Exception as e:
        logger.error(f'[BILLING] cancel error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('billing.index'))
