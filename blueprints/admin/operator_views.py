"""어드민 - 운영사(operator) 및 팀원/브랜드 배정 관리"""
import logging
from flask import render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required
from blueprints.admin import admin_bp
from models import require_superadmin
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


@admin_bp.route('/operators')
@login_required
@require_superadmin
def operators():
    supabase = current_app.supabase
    try:
        ops = supabase.table('operators').select('*').order('created_at', desc=True).execute()
        result = []
        for op in (ops.data or []):
            user_count = supabase.table('users').select('id', count='exact').eq(
                'operator_id', op['id']
            ).execute()
            brand_count = supabase.table('brand_profiles').select('id', count='exact').eq(
                'operator_id', op['id']
            ).execute()
            op['user_count'] = user_count.count or 0
            op['brand_count'] = brand_count.count or 0
            result.append(op)
    except Exception as e:
        logger.error(f'[ADMIN] operators error: {e}')
        result = []
    return render_template('admin/operators.html', operators=result)


@admin_bp.route('/operators/new', methods=['GET', 'POST'])
@login_required
@require_superadmin
def operator_new():
    if request.method == 'GET':
        return render_template('admin/operator_edit.html', operator=None)

    name = request.form.get('name', '').strip()
    plan_type = request.form.get('plan_type', 'pro')
    max_brands = request.form.get('max_brands', 10, type=int)
    max_users = request.form.get('max_users', 20, type=int)

    if not name:
        flash('운영사명을 입력하세요.', 'warning')
        return render_template('admin/operator_edit.html', operator=None)

    supabase = current_app.supabase
    try:
        supabase.table('operators').insert({
            'name': name,
            'plan_type': plan_type,
            'max_brands': max_brands,
            'max_users': max_users,
            'created_at': now_kst().isoformat(),
            'updated_at': now_kst().isoformat(),
        }).execute()
        flash(f'운영사 "{name}"이 생성되었습니다.', 'success')
        return redirect(url_for('admin.operators'))
    except Exception as e:
        logger.error(f'[ADMIN] operator_new error: {e}')
        flash('생성 중 오류가 발생했습니다.', 'danger')
        return render_template('admin/operator_edit.html', operator=None)


@admin_bp.route('/operators/<op_id>')
@login_required
@require_superadmin
def operator_detail(op_id):
    supabase = current_app.supabase
    try:
        op_r = supabase.table('operators').select('*').eq('id', op_id).execute()
        if not op_r.data:
            flash('운영사를 찾을 수 없습니다.', 'warning')
            return redirect(url_for('admin.operators'))
        op = op_r.data[0]

        members = supabase.table('users').select(
            'id, email, name, site_role, is_active, created_at'
        ).eq('operator_id', op_id).order('created_at').execute()

        brands = supabase.table('brand_profiles').select(
            'id, name, is_default, created_at'
        ).eq('operator_id', op_id).order('created_at').execute()

        # 각 팀원의 브랜드 배정 현황
        access_r = supabase.table('user_brand_access').select('user_id, brand_id').execute()
        access_map = {}
        for row in (access_r.data or []):
            access_map.setdefault(row['user_id'], []).append(row['brand_id'])

    except Exception as e:
        logger.error(f'[ADMIN] operator_detail error: {e}')
        flash('오류가 발생했습니다.', 'danger')
        return redirect(url_for('admin.operators'))

    return render_template('admin/operator_detail.html',
                           op=op,
                           members=members.data or [],
                           brands=brands.data or [],
                           access_map=access_map)


@admin_bp.route('/operators/<op_id>/invite', methods=['POST'])
@login_required
@require_superadmin
def operator_invite(op_id):
    """기존 유저를 operator에 추가하거나 새 계정 생성"""
    email = request.form.get('email', '').strip().lower()
    site_role = request.form.get('site_role', 'user')

    if not email:
        flash('이메일을 입력하세요.', 'warning')
        return redirect(url_for('admin.operator_detail', op_id=op_id))

    supabase = current_app.supabase
    try:
        user_r = supabase.table('users').select('id, email').eq('email', email).execute()
        if user_r.data:
            supabase.table('users').update({
                'operator_id': op_id,
                'site_role': site_role,
                'updated_at': now_kst().isoformat(),
            }).eq('id', user_r.data[0]['id']).execute()
            flash(f'{email} 님을 팀에 추가했습니다.', 'success')
        else:
            flash(f'{email} 계정이 없습니다. 먼저 회원가입 후 추가하세요.', 'warning')
    except Exception as e:
        logger.error(f'[ADMIN] operator_invite error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin.operator_detail', op_id=op_id))


@admin_bp.route('/operators/<op_id>/remove-user/<user_id>', methods=['POST'])
@login_required
@require_superadmin
def operator_remove_user(op_id, user_id):
    supabase = current_app.supabase
    try:
        supabase.table('user_brand_access').delete().eq('user_id', user_id).execute()
        supabase.table('users').update({
            'operator_id': None,
            'site_role': 'user',
            'updated_at': now_kst().isoformat(),
        }).eq('id', user_id).execute()
        flash('팀원을 제거했습니다.', 'info')
    except Exception as e:
        logger.error(f'[ADMIN] remove_user error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin.operator_detail', op_id=op_id))


@admin_bp.route('/operators/<op_id>/assign-brand', methods=['POST'])
@login_required
@require_superadmin
def operator_assign_brand(op_id):
    """팀원에게 특정 브랜드 배정 (없으면 전체 접근)"""
    user_id = request.form.get('user_id')
    brand_ids = request.form.getlist('brand_ids')

    if not user_id:
        flash('팀원을 선택하세요.', 'warning')
        return redirect(url_for('admin.operator_detail', op_id=op_id))

    supabase = current_app.supabase
    try:
        # 기존 배정 초기화
        supabase.table('user_brand_access').delete().eq('user_id', user_id).execute()
        # 새 배정
        if brand_ids:
            rows = [{'user_id': user_id, 'brand_id': bid,
                     'created_at': now_kst().isoformat()} for bid in brand_ids]
            supabase.table('user_brand_access').insert(rows).execute()
            flash(f'{len(brand_ids)}개 브랜드 배정 완료.', 'success')
        else:
            flash('브랜드 배정이 초기화되었습니다 (전체 접근).', 'info')
    except Exception as e:
        logger.error(f'[ADMIN] assign_brand error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin.operator_detail', op_id=op_id))
