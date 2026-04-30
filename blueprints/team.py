"""팀 관리 — operator admin 전용 (초대 코드, 팀원 목록, 역할 변경)"""
import logging
import random
import string
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)
team_bp = Blueprint('team', __name__)


def _require_operator_admin():
    """operator admin 이 아니면 대시보드로 리다이렉트 (None 반환 시 통과)"""
    if not current_user.operator_id or not current_user.is_operator_admin:
        flash('팀 관리는 기업 계정 관리자만 사용할 수 있습니다.', 'warning')
        return redirect(url_for('main.dashboard'))
    return None


def _generate_invite_code(length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))


# ──────────────────────────────────────────
# 팀 관리 메인
# ──────────────────────────────────────────
@team_bp.route('/')
@login_required
def index():
    redir = _require_operator_admin()
    if redir:
        return redir

    supabase = current_app.supabase
    op_id = current_user.operator_id

    try:
        # 운영사 정보
        op_r = supabase.table('operators').select('*').eq('id', op_id).execute()
        operator = op_r.data[0] if op_r.data else {}

        # 팀원 목록
        members_r = supabase.table('users').select(
            'id, email, name, site_role, is_active, created_at'
        ).eq('operator_id', op_id).order('created_at').execute()
        members = members_r.data or []

        # 브랜드 목록
        brands_r = supabase.table('brand_profiles').select(
            'id, name, is_default'
        ).eq('operator_id', op_id).order('created_at').execute()
        brands = brands_r.data or []

    except Exception as e:
        logger.error(f'[TEAM] index error: {e}')
        operator, members, brands = {}, [], []

    return render_template('team/index.html',
                           operator=operator,
                           members=members,
                           brands=brands)


# ──────────────────────────────────────────
# 초대 코드 재발급
# ──────────────────────────────────────────
@team_bp.route('/regenerate-invite', methods=['POST'])
@login_required
def regenerate_invite():
    redir = _require_operator_admin()
    if redir:
        return redir

    supabase = current_app.supabase
    new_code = _generate_invite_code(8)
    try:
        supabase.table('operators').update({
            'invite_code': new_code,
            'updated_at': now_kst().isoformat(),
        }).eq('id', current_user.operator_id).execute()
        flash(f'새 초대 코드가 발급되었습니다: {new_code}', 'success')
    except Exception as e:
        logger.error(f'[TEAM] regenerate_invite error: {e}')
        flash('초대 코드 재발급 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('team.index'))


# ──────────────────────────────────────────
# 팀원 역할 변경 (관리자 <-> 일반)
# ──────────────────────────────────────────
@team_bp.route('/member/<member_id>/role', methods=['POST'])
@login_required
def change_role(member_id):
    redir = _require_operator_admin()
    if redir:
        return redir

    supabase = current_app.supabase
    op_id = current_user.operator_id

    # 해당 유저가 내 operator 소속인지 확인
    try:
        user_r = supabase.table('users').select('id, site_role, operator_id').eq(
            'id', member_id
        ).execute()
        if not user_r.data or user_r.data[0].get('operator_id') != op_id:
            flash('해당 팀원을 찾을 수 없습니다.', 'warning')
            return redirect(url_for('team.index'))

        member = user_r.data[0]
        # 자기 자신 역할 변경 금지
        if str(member_id) == str(current_user.id):
            flash('자신의 역할은 변경할 수 없습니다.', 'warning')
            return redirect(url_for('team.index'))

        new_role = request.form.get('role', 'user')
        if new_role not in ('operator_admin', 'user'):
            new_role = 'user'

        supabase.table('users').update({
            'site_role': new_role,
            'updated_at': now_kst().isoformat(),
        }).eq('id', member_id).execute()

        role_label = '관리자' if new_role == 'operator_admin' else '팀원'
        flash(f'역할이 {role_label}(으)로 변경되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[TEAM] change_role error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('team.index'))


# ──────────────────────────────────────────
# 팀원 내보내기 (operator에서 제거)
# ──────────────────────────────────────────
@team_bp.route('/member/<member_id>/remove', methods=['POST'])
@login_required
def remove_member(member_id):
    redir = _require_operator_admin()
    if redir:
        return redir

    supabase = current_app.supabase
    op_id = current_user.operator_id

    if str(member_id) == str(current_user.id):
        flash('자신을 팀에서 내보낼 수 없습니다.', 'warning')
        return redirect(url_for('team.index'))

    try:
        # 소속 확인
        user_r = supabase.table('users').select('id, operator_id').eq(
            'id', member_id
        ).execute()
        if not user_r.data or user_r.data[0].get('operator_id') != op_id:
            flash('해당 팀원을 찾을 수 없습니다.', 'warning')
            return redirect(url_for('team.index'))

        supabase.table('users').update({
            'operator_id': None,
            'site_role': 'user',
            'updated_at': now_kst().isoformat(),
        }).eq('id', member_id).execute()

        flash('팀원을 내보냈습니다.', 'info')
    except Exception as e:
        logger.error(f'[TEAM] remove_member error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('team.index'))
