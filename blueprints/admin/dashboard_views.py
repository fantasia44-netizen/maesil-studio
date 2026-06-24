"""어드민 대시보드"""
import logging
from datetime import datetime, timedelta, timezone
from flask import render_template, request, current_app
from flask_login import login_required, current_user
from blueprints.admin import admin_bp
from models import require_superadmin
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


@admin_bp.before_request
@login_required
def check_superadmin():
    # view-as 모드일 때도 어드민 라우트 허용 (실제 어드민이 세션에 있음)
    from flask import session, abort
    if session.get('view_as_admin_id'):
        return
    if not current_user.is_superadmin:
        abort(403)


def _fetch_revenue_summary(supabase) -> dict:
    """이달 결제 집계 + 오늘 현황 + 구독 상태 카운트."""
    now     = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    result = {
        'revenue_today': 0, 'paid_today': 0, 'failed_today': 0,
        'revenue_mtd':   0, 'refund_mtd': 0, 'net_mtd': 0,
        'sub_active': 0, 'sub_trial': 0, 'sub_past_due': 0,
        'sub_cancelled': 0, 'sub_expired': 0,
        'mrr_forecast': 0,
    }

    # ── 이달 결제 ──
    try:
        rows = supabase.table('payments') \
            .select('amount,refund_amount,refund_status,status,paid_at') \
            .gte('paid_at', month_start) \
            .execute().data or []
        for r in rows:
            amt = int(r.get('amount') or 0)
            if r.get('status') == 'paid':
                result['revenue_mtd'] += amt
                if r.get('paid_at', '') >= today_start:
                    result['revenue_today'] += amt
                    result['paid_today']    += 1
                if r.get('refund_status') == 'completed':
                    result['refund_mtd'] += int(r.get('refund_amount') or 0)
            elif r.get('status') == 'failed':
                if r.get('paid_at', '') >= today_start:
                    result['failed_today'] += 1
        result['net_mtd'] = result['revenue_mtd'] - result['refund_mtd']
    except Exception as e:
        logger.warning(f'[ADMIN] revenue_summary payments 실패: {e}')

    # ── 구독 상태 분포 ──
    try:
        from models import PLAN_FEATURES
        subs = supabase.table('subscriptions') \
            .select('status,plan_type,auto_renewal') \
            .execute().data or []
        for s in subs:
            st = s.get('status', '')
            if st == 'active':
                result['sub_active'] += 1
                if s.get('auto_renewal'):
                    price = int(PLAN_FEATURES.get(s.get('plan_type', ''), {}).get('price', 0) or 0)
                    result['mrr_forecast'] += price
            elif st == 'trial':    result['sub_trial']     += 1
            elif st == 'past_due': result['sub_past_due']  += 1
            elif st in ('cancelled', 'canceled'):
                result['sub_cancelled'] += 1
            elif st == 'expired':  result['sub_expired']   += 1
    except Exception as e:
        logger.warning(f'[ADMIN] revenue_summary subs 실패: {e}')

    return result


@admin_bp.route('/')
@login_required
@require_superadmin
def dashboard():
    supabase = current_app.supabase
    stats = {
        'total_users': 0,
        'active_subscriptions': 0,
        'total_creations': 0,
        'plan_dist': {},
        'online_count': 0,
    }
    recent_users = []
    recent_payments = []
    online_users = []

    revenue = _fetch_revenue_summary(supabase)

    # 환불 대기 건 (refund_status = 'requested') — 날짜 무관 전체
    refund_queue = []
    try:
        rq = supabase.table('payments') \
            .select('id,payment_id,user_id,amount,order_name,refund_reason,refund_requested_at,paid_at') \
            .eq('refund_status', 'requested') \
            .order('refund_requested_at', desc=True) \
            .limit(20) \
            .execute()
        refund_queue = rq.data or []
    except Exception as e:
        logger.warning(f'[ADMIN] refund_queue 조회 실패: {e}')

    try:
        users_r = supabase.table('users').select('id', count='exact').execute()
        stats['total_users'] = users_r.count or 0

        sub_r = supabase.table('subscriptions').select('id', count='exact').eq('status', 'active').execute()
        stats['active_subscriptions'] = sub_r.count or 0

        cr_r = supabase.table('creations').select('id', count='exact').execute()
        stats['total_creations'] = cr_r.count or 0

        # 플랜 분포
        plans_r = supabase.table('users').select('plan_type').execute()
        for row in (plans_r.data or []):
            p = row.get('plan_type', 'free')
            stats['plan_dist'][p] = stats['plan_dist'].get(p, 0) + 1

        # 접속 중인 유저 (5분 이내 last_seen_at)
        cutoff = (now_kst() - timedelta(minutes=5)).isoformat()
        online_r = supabase.table('users').select(
            'id, email, name, plan_type, last_seen_at'
        ).gte('last_seen_at', cutoff).order('last_seen_at', desc=True).limit(20).execute()
        online_users = online_r.data or []
        stats['online_count'] = len(online_users)

        # 최근 가입자
        ru = supabase.table('users').select(
            'id, email, name, plan_type, last_seen_at, created_at'
        ).order('created_at', desc=True).limit(20).execute()
        recent_users = ru.data or []

        # 최근 가입자 구독 정보 (status + 만료일) 병합
        if recent_users:
            user_ids = [u['id'] for u in recent_users]
            try:
                sub_r = supabase.table('subscriptions') \
                    .select('user_id, status, current_period_end, plan_type') \
                    .in_('user_id', user_ids) \
                    .in_('status', ['active', 'trial', 'past_due', 'cancelled', 'expired']) \
                    .order('current_period_end', desc=True) \
                    .execute()
                # user_id → 최신 구독 한 건 매핑
                sub_map: dict = {}
                for s in (sub_r.data or []):
                    uid = s['user_id']
                    if uid not in sub_map:
                        sub_map[uid] = s
                for u in recent_users:
                    sub = sub_map.get(u['id'], {})
                    u['sub_status']  = sub.get('status', '')
                    expires = sub.get('current_period_end', '')
                    # current_period_end 없는 trial은 created_at + 30일로 추정
                    if not expires and sub.get('status') == 'trial':
                        created = sub.get('created_at') or u.get('created_at', '')
                        if created:
                            from datetime import datetime, timedelta, timezone
                            try:
                                dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                                expires = (dt + timedelta(days=30)).isoformat()
                            except Exception:
                                pass
                    u['sub_expires'] = expires
            except Exception as sub_err:
                logger.warning(f'[ADMIN] 구독 정보 병합 실패: {sub_err}')
                for u in recent_users:
                    u['sub_status'] = ''
                    u['sub_expires'] = ''

        # 최근 결제
        rp = supabase.table('payments').select(
            'id, user_id, payment_type, plan_type, points_granted, amount, status, paid_at'
        ).order('paid_at', desc=True).limit(10).execute()
        recent_payments = rp.data or []

    except Exception as e:
        logger.error(f'[ADMIN] dashboard error: {e}')

    return render_template('admin/dashboard.html',
                           stats=stats,
                           revenue=revenue,
                           refund_queue=refund_queue,
                           recent_users=recent_users,
                           recent_payments=recent_payments,
                           online_users=online_users,
                           today_str=now_kst().strftime('%Y-%m-%d'))
