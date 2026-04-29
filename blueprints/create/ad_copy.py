"""광고 카피 생성"""
from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from blueprints.create import create_bp
from blueprints.create._base import get_default_brand, get_brand_by_id, run_text_generation


@create_bp.route('/ad-copy', methods=['GET'])
@login_required
def ad_copy():
    supabase = current_app.supabase
    brands = supabase.table('brand_profiles').select('id, name, is_default').eq(
        'user_id', current_user.id
    ).execute()
    default_brand = get_default_brand(supabase)
    if not default_brand:
        flash('먼저 브랜드 프로필을 등록해 주세요.', 'warning')
        return redirect(url_for('main.onboarding'))
    return render_template('create/ad_copy.html',
                           brands=brands.data or [],
                           default_brand=default_brand)


@create_bp.route('/ad-copy/generate', methods=['POST'])
@login_required
def ad_copy_generate():
    supabase = current_app.supabase
    brand_id = request.form.get('brand_id', '')
    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    input_data = {
        'product': request.form.get('product', ''),
        'target': request.form.get('target', ''),
        'goal': request.form.get('goal', '구매'),
        'platform': request.form.get('platform', '인스타그램'),
    }

    from services.prompts.thumbnail import build_ad_copy_prompt
    system, user = build_ad_copy_prompt(brand, input_data)
    result = run_text_generation('ad_copy', brand, input_data, system, user)
    return jsonify(result)


@create_bp.route('/hub')
@login_required
def hub():
    """생성 허브 (이미지 생성 카테고리)"""
    return render_template('create/hub.html')
