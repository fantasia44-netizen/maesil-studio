"""어드민 대시보드"""
import logging
from flask import render_template, request, current_app
from flask_login import login_required, current_user
from blueprints.admin import admin_bp
from models import require_superadmin

logger = logging.getLogger(__name__)


@admin_bp.before_request
@login_required
def check_superadmin():
    if not current_user.is_superadmin:
        from flask import abort
        abort(403)


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
    }
    recent_users = []
    recent_payments = []

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

        # 최근 가입자
        ru = supabase.table('users').select(
            'id, email, name, plan_type, created_at'
        ).order('created_at', desc=True).limit(10).execute()
        recent_users = ru.data or []

        # 최근 결제
        rp = supabase.table('payments').select(
            'id, user_id, payment_type, plan_type, points_granted, amount, status, paid_at'
        ).order('paid_at', desc=True).limit(10).execute()
        recent_payments = rp.data or []

    except Exception as e:
        logger.error(f'[ADMIN] dashboard error: {e}')

    return render_template('admin/dashboard.html',
                           stats=stats,
                           recent_users=recent_users,
                           recent_payments=recent_payments)
