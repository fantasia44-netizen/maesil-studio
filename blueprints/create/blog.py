"""블로그 포스트 생성"""
from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from blueprints.create import create_bp
from blueprints.create._base import get_default_brand, get_brand_by_id, run_text_generation


@create_bp.route('/blog', methods=['GET'])
@login_required
def blog():
    supabase = current_app.supabase
    brands = supabase.table('brand_profiles').select('id, name, is_default').eq(
        'user_id', current_user.id
    ).execute()
    default_brand = get_default_brand(supabase)
    if not default_brand:
        flash('먼저 브랜드 프로필을 등록해 주세요.', 'warning')
        return redirect(url_for('main.onboarding'))
    return render_template('create/blog.html',
                           brands=brands.data or [],
                           default_brand=default_brand)


@create_bp.route('/blog/generate', methods=['POST'])
@login_required
def blog_generate():
    supabase = current_app.supabase
    brand_id = request.form.get('brand_id', '')
    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    input_data = {
        'topic': request.form.get('topic', ''),
        'purpose': request.form.get('purpose', '정보제공'),
        'length': request.form.get('length', '2000'),
        'seo_keywords': request.form.get('seo_keywords', ''),
    }

    from services.prompts.blog import build_prompt
    system, user = build_prompt(brand, input_data)
    result = run_text_generation('blog', brand, input_data, system, user)
    return jsonify(result)
