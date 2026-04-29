"""어드민 - 사용자 관리"""
import logging
from flask import render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required
from blueprints.admin import admin_bp
from models import require_superadmin, PLAN_FEATURES
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


@admin_bp.route('/users')
@login_required
@require_superadmin
def users():
    supabase = current_app.supabase
    page = request.args.get('page', 1, type=int)
    search = request.args.get('q', '').strip()
    plan_filter = request.args.get('plan', '')
    per_page = 20
    offset = (page - 1) * per_page

    try:
        q = supabase.table('users').select('*')
        if search:
            q = q.ilike('email', f'%{search}%')
        if plan_filter:
            q = q.eq('plan_type', plan_filter)
        result = q.order('created_at', desc=True).range(offset, offset + per_page - 1).execute()
        users_list = result.data or []
    except Exception as e:
        logger.error(f'[ADMIN] users error: {e}')
        users_list = []

    return render_template('admin/users.html',
                           users=users_list,
                           page=page,
                           search=search,
                           plan_filter=plan_filter,
                           PLAN_FEATURES=PLAN_FEATURES)


@admin_bp.route('/users/<user_id>')
@login_required
@require_superadmin
def user_detail(user_id):
    supabase = current_app.supabase
    try:
        user_r = supabase.table('users').select('*').eq('id', user_id).execute()
        if not user_r.data:
            flash('사용자를 찾을 수 없습니다.', 'warning')
            return redirect(url_for('admin.users'))
        user = user_r.data[0]

        from services.point_service import get_balance, get_ledger
        balance = get_balance(user_id)
        ledger = get_ledger(user_id, limit=20)

        creations_r = supabase.table('creations').select(
            'id, creation_type, status, points_used, created_at'
        ).eq('user_id', user_id).order('created_at', desc=True).limit(20).execute()

        payments_r = supabase.table('payments').select('*').eq(
            'user_id', user_id
        ).order('created_at', desc=True).limit(10).execute()

    except Exception as e:
        logger.error(f'[ADMIN] user_detail error: {e}')
        flash('오류가 발생했습니다.', 'danger')
        return redirect(url_for('admin.users'))

    return render_template('admin/user_detail.html',
                           user=user,
                           balance=balance,
                           ledger=ledger,
                           creations=creations_r.data or [],
                           payments=payments_r.data or [],
                           PLAN_FEATURES=PLAN_FEATURES)


@admin_bp.route('/users/<user_id>/set-plan', methods=['POST'])
@login_required
@require_superadmin
def set_user_plan(user_id):
    plan_type = request.form.get('plan_type', '')
    if plan_type not in PLAN_FEATURES:
        flash('유효하지 않은 플랜입니다.', 'danger')
        return redirect(url_for('admin.user_detail', user_id=user_id))

    supabase = current_app.supabase
    try:
        supabase.table('users').update({
            'plan_type': plan_type,
            'updated_at': now_kst().isoformat(),
        }).eq('id', user_id).execute()
        flash(f'플랜이 {plan_type}으로 변경되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[ADMIN] set_plan error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin.user_detail', user_id=user_id))


@admin_bp.route('/users/<user_id>/add-points', methods=['POST'])
@login_required
@require_superadmin
def add_user_points(user_id):
    amount = request.form.get('amount', 0, type=int)
    note = request.form.get('note', '관리자 지급')
    if amount <= 0:
        flash('포인트 수량을 입력하세요.', 'warning')
        return redirect(url_for('admin.user_detail', user_id=user_id))

    try:
        from services.point_service import add_points
        new_balance = add_points(user_id, amount, 'refund', note=note)
        flash(f'{amount:,}P 지급 완료 (잔액: {new_balance:,}P)', 'success')
    except Exception as e:
        logger.error(f'[ADMIN] add_points error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin.user_detail', user_id=user_id))


@admin_bp.route('/users/<user_id>/deactivate', methods=['POST'])
@login_required
@require_superadmin
def deactivate_user(user_id):
    supabase = current_app.supabase
    try:
        supabase.table('users').update({
            'is_active': False,
            'updated_at': now_kst().isoformat(),
        }).eq('id', user_id).execute()
        flash('계정이 비활성화되었습니다.', 'info')
    except Exception as e:
        logger.error(f'[ADMIN] deactivate error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin.user_detail', user_id=user_id))
