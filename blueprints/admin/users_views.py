"""어드민 - 사용자 관리"""
import logging
from flask import render_template, request, redirect, url_for, flash, current_app, session, jsonify
from flask_login import login_required, current_user
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
    status_filter = request.args.get('status', '')  # active | inactive | locked | online
    per_page = 20
    offset = (page - 1) * per_page

    try:
        q = supabase.table('users').select(
            'id, email, name, plan_type, is_active, site_role, operator_id, '
            'last_seen_at, failed_login_count, locked_until, created_at'
        )
        if search:
            q = q.ilike('email', f'%{search}%')
        if plan_filter:
            q = q.eq('plan_type', plan_filter)
        if status_filter == 'active':
            q = q.eq('is_active', True)
        elif status_filter == 'inactive':
            q = q.eq('is_active', False)
        elif status_filter == 'locked':
            q = q.gt('failed_login_count', 0)
        elif status_filter == 'online':
            from services.tz_utils import ensure_aware
            from datetime import datetime, timedelta
            cutoff = (now_kst() - timedelta(minutes=5)).isoformat()
            q = q.gte('last_seen_at', cutoff)

        result = q.order('created_at', desc=True).range(offset, offset + per_page - 1).execute()
        users_list = result.data or []
    except Exception as e:
        logger.error(f'[ADMIN] users error: {e}')
        users_list = []

    # 접속 중 여부 (last_seen_at 기준 5분 이내)
    from datetime import datetime, timedelta
    from services.tz_utils import ensure_aware
    now = now_kst()
    online_cutoff = now - timedelta(minutes=5)
    for u in users_list:
        raw = u.get('last_seen_at')
        if raw:
            try:
                ts = ensure_aware(datetime.fromisoformat(raw))
                diff_min = int((now - ts).total_seconds() / 60)
                u['_is_online'] = ts >= online_cutoff
                u['_ago_min'] = diff_min
            except Exception:
                u['_is_online'] = False
                u['_ago_min'] = None
        else:
            u['_is_online'] = False
            u['_ago_min'] = None

    # 현재 view-as 대상 ID (테이블에서 하이라이트용)
    view_as_uid = session.get('view_as_user_id')

    return render_template('admin/users.html',
                           users=users_list,
                           page=page,
                           search=search,
                           plan_filter=plan_filter,
                           status_filter=status_filter,
                           view_as_uid=view_as_uid,
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

    view_as_uid = session.get('view_as_user_id')

    # 구독 상태 (operator 풀 우선 — 포인트 스코프와 동일). is_active(계정 정지)와 별개 축.
    sub_status = None
    sub_end = None
    sub_active = False
    try:
        op_id = user.get('operator_id')
        sq = supabase.table('subscriptions').select('status, current_period_end')
        if op_id:
            sq = sq.eq('operator_id', op_id)
        else:
            sq = sq.is_('operator_id', 'null').eq('user_id', user_id)
        sr = sq.order('created_at', desc=True).limit(1).execute()
        if sr.data:
            sub_status = sr.data[0].get('status')
            sub_end = sr.data[0].get('current_period_end')
        from models import User as _U
        _chk = dict(user)
        if sub_status is not None:
            _chk['subscription_status'] = sub_status
        _chk['current_period_end'] = sub_end
        sub_active = _U(_chk).is_subscription_active
    except Exception as e:
        logger.warning(f'[ADMIN] 구독 상태 조회 실패 user={user_id}: {e}')

    return render_template('admin/user_detail.html',
                           user=user,
                           balance=balance,
                           ledger=ledger,
                           creations=creations_r.data or [],
                           payments=payments_r.data or [],
                           view_as_uid=view_as_uid,
                           sub_status=sub_status,
                           sub_end=sub_end,
                           sub_active=sub_active,
                           PLAN_FEATURES=PLAN_FEATURES)


# ── 플랜 변경 ──────────────────────────────────────────────────────────────
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


# ── 포인트 지급 ────────────────────────────────────────────────────────────
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


# ── 비활성화 / 활성화 ──────────────────────────────────────────────────────
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


@admin_bp.route('/users/<user_id>/activate', methods=['POST'])
@login_required
@require_superadmin
def activate_user(user_id):
    supabase = current_app.supabase
    try:
        supabase.table('users').update({
            'is_active': True,
            'updated_at': now_kst().isoformat(),
        }).eq('id', user_id).execute()
        flash('계정이 활성화되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[ADMIN] activate error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin.user_detail', user_id=user_id))


# ── 로그인 잠금 해제 ───────────────────────────────────────────────────────
@admin_bp.route('/users/<user_id>/unlock', methods=['POST'])
@login_required
@require_superadmin
def unlock_user(user_id):
    supabase = current_app.supabase
    try:
        supabase.table('users').update({
            'failed_login_count': 0,
            'locked_until': None,
            'updated_at': now_kst().isoformat(),
        }).eq('id', user_id).execute()
        flash('로그인 잠금이 해제되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[ADMIN] unlock error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin.user_detail', user_id=user_id))


# ── 유저로 보기 (Impersonate) ──────────────────────────────────────────────
@admin_bp.route('/users/<user_id>/view-as')
@login_required
@require_superadmin
def view_as_user(user_id):
    """슈퍼어드민이 특정 유저의 플랜·팀 컨텍스트로 서비스를 탐색."""
    supabase = current_app.supabase
    try:
        res = supabase.table('users').select(
            'id, email, name, plan_type, is_active'
        ).eq('id', user_id).execute()
        if not res.data:
            flash('사용자를 찾을 수 없습니다.', 'warning')
            return redirect(url_for('admin.users'))
        target = res.data[0]
    except Exception as e:
        logger.error(f'[ADMIN] view_as error: {e}')
        flash('오류가 발생했습니다.', 'danger')
        return redirect(url_for('admin.users'))

    session['view_as_user_id'] = user_id
    # current_user.get_id() 는 이전 view-as 로 캐시가 오염됐을 때 target_id 를 반환할 수 있음.
    # Flask-Login 이 로그인 시 저장한 session['_user_id'] (원본 로그인 ID) 를 사용.
    session['view_as_admin_id'] = session.get('_user_id') or current_user.get_id()
    session['view_as_user_email'] = target.get('email', user_id)
    logger.info(f'[ADMIN] view-as user {user_id} ({target.get("email")}) by {current_user.email}')
    flash(f'현재 {target["email"]} 으로 보는 중입니다.', 'warning')
    return redirect(url_for('main.dashboard'))


@admin_bp.route('/users/exit-view-as')
@login_required
def exit_view_as():
    """유저로 보기 종료 — 어드민 원래 상태로 복귀."""
    uid = session.pop('view_as_user_id', None)
    admin_id = session.pop('view_as_admin_id', None)
    session.pop('view_as_user_email', None)
    # 캐시에 남은 수정된 어드민 user 객체를 즉시 무효화 — 다음 요청에서 DB에서 새로 로드
    if admin_id:
        user_cache = getattr(current_app, 'user_cache', {})
        user_cache.pop(admin_id, None)
    if uid:
        return redirect(url_for('admin.user_detail', user_id=uid))
    return redirect(url_for('admin.users'))
