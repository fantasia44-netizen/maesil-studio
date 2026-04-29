"""브랜드 프로필 CRUD"""
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)
brand_bp = Blueprint('brand', __name__)


def _get_user_brands(supabase):
    result = supabase.table('brand_profiles').select('*').eq(
        'user_id', current_user.id
    ).order('is_default', desc=True).order('created_at', desc=False).execute()
    return result.data or []


def _parse_list_field(value: str) -> list:
    return [v.strip() for v in value.split(',') if v.strip()]


def _max_brands() -> int:
    from models import PLAN_FEATURES
    return PLAN_FEATURES.get(current_user.plan_type, {}).get('brand_profiles', 1)


@brand_bp.route('/')
@login_required
def index():
    supabase = current_app.supabase
    brands = _get_user_brands(supabase)
    return render_template('brand/index.html', brands=brands, max_brands=_max_brands())


@brand_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    supabase = current_app.supabase

    # 플랜 한도 체크
    brands = _get_user_brands(supabase)
    if len(brands) >= _max_brands():
        flash(f'현재 플랜에서는 브랜드 프로필을 최대 {_max_brands()}개까지 등록할 수 있습니다.', 'warning')
        return redirect(url_for('brand.index'))

    if request.method == 'GET':
        return render_template('brand/edit.html', brand=None)

    return _save_brand(supabase, brand_id=None)


@brand_bp.route('/<brand_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(brand_id):
    supabase = current_app.supabase
    result = supabase.table('brand_profiles').select('*').eq('id', brand_id).eq(
        'user_id', current_user.id
    ).execute()
    if not result.data:
        flash('브랜드 프로필을 찾을 수 없습니다.', 'warning')
        return redirect(url_for('brand.index'))

    brand = result.data[0]
    if request.method == 'GET':
        return render_template('brand/edit.html', brand=brand)

    return _save_brand(supabase, brand_id=brand_id)


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
            supabase.table('brand_profiles').update(data).eq('id', brand_id).eq(
                'user_id', current_user.id
            ).execute()
            flash('브랜드 프로필이 업데이트되었습니다.', 'success')
        else:
            data['user_id'] = current_user.id
            data['is_default'] = False
            data['created_at'] = now_kst().isoformat()
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
    supabase = current_app.supabase
    try:
        # 기존 default 해제
        supabase.table('brand_profiles').update({'is_default': False}).eq(
            'user_id', current_user.id
        ).execute()
        # 새 default 설정
        supabase.table('brand_profiles').update({'is_default': True}).eq(
            'id', brand_id
        ).eq('user_id', current_user.id).execute()
        flash('기본 브랜드가 변경되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[BRAND] set_default error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('brand.index'))


@brand_bp.route('/<brand_id>/delete', methods=['POST'])
@login_required
def delete(brand_id):
    supabase = current_app.supabase
    try:
        # 기본 브랜드면 삭제 방지
        result = supabase.table('brand_profiles').select('is_default').eq(
            'id', brand_id
        ).eq('user_id', current_user.id).execute()
        if result.data and result.data[0].get('is_default'):
            flash('기본 브랜드는 삭제할 수 없습니다. 먼저 다른 브랜드를 기본으로 설정하세요.', 'warning')
            return redirect(url_for('brand.index'))

        supabase.table('brand_profiles').delete().eq('id', brand_id).eq(
            'user_id', current_user.id
        ).execute()
        flash('브랜드 프로필이 삭제되었습니다.', 'info')
    except Exception as e:
        logger.error(f'[BRAND] delete error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('brand.index'))
