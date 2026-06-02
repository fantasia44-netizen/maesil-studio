"""상세페이지 카피 생성"""
from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from blueprints.create import create_bp
from blueprints.create._base import (
    get_default_brand, get_brand_by_id, run_text_generation, get_accessible_brands
)


def _recent_detail_page_creations(supabase, user_id: str, brand_id, limit: int = 5):
    q = supabase.table('creations').select(
        'id, output_data, input_data, created_at'
    ).eq('user_id', user_id).eq('status', 'done').eq('creation_type', 'detail_page')
    if brand_id:
        q = q.eq('brand_id', brand_id)
    try:
        return q.order('created_at', desc=True).limit(limit).execute().data or []
    except Exception:
        return []


@create_bp.route('/detail-page', methods=['GET'])
@login_required
def detail_page():
    supabase      = current_app.supabase
    brands        = get_accessible_brands(supabase)
    default_brand = get_default_brand(supabase)
    if not default_brand:
        flash('먼저 브랜드 프로필을 등록해 주세요.', 'warning')
        return redirect(url_for('main.onboarding'))
    return render_template('create/detail_page.html',
                           brands=brands,
                           default_brand=default_brand)


@create_bp.route('/detail-page/generate', methods=['POST'])
@login_required
def detail_page_generate():
    supabase = current_app.supabase
    data = request.get_json(silent=True) or {}
    brand_id = (data.get('brand_id') or '').strip()
    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    input_data = {
        'product_name':   (data.get('product_name')   or '').strip(),
        'features':       (data.get('features')       or '').strip(),
        'price_range':    (data.get('price_range')    or '').strip(),
        'differentiator': (data.get('differentiator') or '').strip(),
    }

    from services.prompts.detail_page import build_prompt
    system, user = build_prompt(brand, input_data)
    result = run_text_generation('detail_page', brand, input_data, system, user)
    return jsonify(result)


@create_bp.route('/detail-page/recent-done', methods=['GET'])
@login_required
def detail_page_recent_done():
    supabase = current_app.supabase
    brand_id = request.args.get('brand_id', '').strip() or None
    items    = _recent_detail_page_creations(supabase, current_user.id, brand_id)
    result   = []
    for r in items:
        od  = r.get('output_data') or {}
        inp = r.get('input_data')  or {}
        result.append({
            'id':           r['id'],
            'product_name': (inp.get('product_name') or '제목 없음')[:40],
            'text_preview': (od.get('text') or '')[:80],
            'created_at':   (r.get('created_at') or '')[:10],
        })
    return jsonify(ok=True, items=result)


@create_bp.route('/detail-page/result/<creation_id>', methods=['GET'])
@login_required
def detail_page_result(creation_id: str):
    supabase = current_app.supabase
    r = supabase.table('creations').select(
        'id, user_id, output_data'
    ).eq('id', creation_id).eq('status', 'done').eq(
        'creation_type', 'detail_page'
    ).execute()
    if not r.data:
        return jsonify(ok=False, message='없음')
    row = r.data[0]
    if row['user_id'] != current_user.id:
        op = getattr(current_user, 'operator_id', None)
        if not op:
            return jsonify(ok=False, message='권한 없음')
    od = row.get('output_data') or {}
    return jsonify(ok=True, text=od.get('text', ''))
