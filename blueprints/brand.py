"""브랜드 프로필 CRUD — operator 구조 지원"""
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from services.tz_utils import now_kst
from blueprints.create._base import get_accessible_brands

logger = logging.getLogger(__name__)
brand_bp = Blueprint('brand', __name__)


def _parse_list_field(value: str) -> list:
    return [v.strip() for v in value.split(',') if v.strip()]


def _max_brands() -> int:
    if current_user.is_superadmin:
        return 9999
    if current_user.operator_id:
        # operator plan 기준
        supabase = current_app.supabase
        op = supabase.table('operators').select('max_brands').eq(
            'id', current_user.operator_id
        ).execute()
        return op.data[0]['max_brands'] if op.data else 10
    from models import PLAN_FEATURES
    return PLAN_FEATURES.get(current_user.plan_type, {}).get('brand_profiles', 1)


@brand_bp.route('/')
@login_required
def index():
    supabase = current_app.supabase
    brands = get_accessible_brands(supabase)
    return render_template('brand/index.html', brands=brands, max_brands=_max_brands())


@brand_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    supabase = current_app.supabase
    if not current_user.is_operator_admin:
        flash('브랜드 등록은 관리자만 가능합니다.', 'warning')
        return redirect(url_for('brand.index'))

    brands = get_accessible_brands(supabase)
    if len(brands) >= _max_brands():
        flash(f'최대 {_max_brands()}개 브랜드까지 등록 가능합니다.', 'warning')
        return redirect(url_for('brand.index'))

    if request.method == 'GET':
        return render_template('brand/edit.html', brand=None)
    return _save_brand(supabase, brand_id=None)


@brand_bp.route('/<brand_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(brand_id):
    supabase = current_app.supabase
    if not current_user.is_operator_admin:
        flash('브랜드 수정은 관리자만 가능합니다.', 'warning')
        return redirect(url_for('brand.index'))

    brand = _get_owned_brand(supabase, brand_id)
    if not brand:
        flash('브랜드를 찾을 수 없습니다.', 'warning')
        return redirect(url_for('brand.index'))

    if request.method == 'GET':
        return render_template('brand/edit.html', brand=brand)
    return _save_brand(supabase, brand_id=brand_id)


def _get_owned_brand(supabase, brand_id: str):
    """수정/삭제 권한 있는 브랜드 조회 (operator 소속 확인)"""
    q = supabase.table('brand_profiles').select('*').eq('id', brand_id)
    if current_user.operator_id:
        q = q.eq('operator_id', current_user.operator_id)
    else:
        q = q.eq('user_id', current_user.id)
    result = q.execute()
    return result.data[0] if result.data else None


def _save_brand(supabase, brand_id):
    data = {
        'name': request.form.get('name', '').strip(),
        'industry': request.form.get('industry', '').strip(),
        'target_customer': request.form.get('target_customer', '').strip(),
        'brand_tone': _parse_list_field(request.form.get('brand_tone', '')),
        'primary_color': request.form.get('primary_color', '').strip(),
        'secondary_color': request.form.get('secondary_color', '').strip(),
        'keywords': _parse_list_field(request.form.get('keywords', '')),
        'avoid_words': _parse_list_field(request.form.get('avoid_words', '')),
        'extra_context': request.form.get('extra_context', '').strip(),
        'updated_at': now_kst().isoformat(),
    }

    if not data['name']:
        flash('브랜드명을 입력하세요.', 'warning')
        return render_template('brand/edit.html', brand=data)

    try:
        if brand_id:
            supabase.table('brand_profiles').update(data).eq('id', brand_id).execute()
            flash('브랜드 프로필이 업데이트되었습니다.', 'success')
        else:
            data['user_id'] = current_user.id
            data['is_default'] = False
            data['created_at'] = now_kst().isoformat()
            if current_user.operator_id:
                data['operator_id'] = current_user.operator_id
            supabase.table('brand_profiles').insert(data).execute()
            flash('브랜드 프로필이 등록되었습니다.', 'success')
        return redirect(url_for('brand.index'))
    except Exception as e:
        logger.error(f'[BRAND] save error: {e}')
        flash('저장 중 오류가 발생했습니다.', 'danger')
        return render_template('brand/edit.html', brand=data)


@brand_bp.route('/<brand_id>/set-default', methods=['POST'])
@login_required
def set_default(brand_id):
    if not current_user.is_operator_admin:
        flash('관리자만 기본 브랜드를 변경할 수 있습니다.', 'warning')
        return redirect(url_for('brand.index'))

    supabase = current_app.supabase
    try:
        if current_user.operator_id:
            supabase.table('brand_profiles').update({'is_default': False}).eq(
                'operator_id', current_user.operator_id
            ).execute()
        else:
            supabase.table('brand_profiles').update({'is_default': False}).eq(
                'user_id', current_user.id
            ).execute()
        supabase.table('brand_profiles').update({'is_default': True}).eq('id', brand_id).execute()
        flash('기본 브랜드가 변경되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[BRAND] set_default error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('brand.index'))


@brand_bp.route('/<brand_id>/delete', methods=['POST'])
@login_required
def delete(brand_id):
    if not current_user.is_operator_admin:
        flash('브랜드 삭제는 관리자만 가능합니다.', 'warning')
        return redirect(url_for('brand.index'))

    supabase = current_app.supabase
    try:
        brand = _get_owned_brand(supabase, brand_id)
        if not brand:
            flash('브랜드를 찾을 수 없습니다.', 'warning')
            return redirect(url_for('brand.index'))
        if brand.get('is_default'):
            flash('기본 브랜드는 삭제할 수 없습니다. 다른 브랜드를 기본으로 설정 후 삭제하세요.', 'warning')
            return redirect(url_for('brand.index'))
        supabase.table('brand_profiles').delete().eq('id', brand_id).execute()
        flash('브랜드 프로필이 삭제되었습니다.', 'info')
    except Exception as e:
        logger.error(f'[BRAND] delete error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('brand.index'))
