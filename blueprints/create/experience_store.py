"""경험 저장소 — 운영자 실제 경험(문제/조치/결과/수치)을 쌓아 블로그 근거로 활용.

blog.py 생성 시 search_relevant 로 주제 매칭 경험을 찾아 프롬프트에 주입한다.
여기서는 경험의 CRUD + 빠른입력(자유 텍스트 → AI 구조화) UI 를 제공.
"""
import logging

from flask import render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from blueprints.create import create_bp
from blueprints.create._base import get_accessible_brands, get_default_brand

logger = logging.getLogger(__name__)

_CATEGORIES = ['광고', '네이버', '쿠팡', '제조', '물류', '3PL', '브랜드',
               'ERP', 'AI', '자금', '인사', '기타']
_CONFIDENTIALITY = ['public', 'anonymized', 'private']


@create_bp.route('/experience-store')
@login_required
def experience_store_page():
    supabase = current_app.supabase
    from services.experience_store import list_records
    brands = get_accessible_brands(supabase) if supabase else []
    default_brand = get_default_brand(supabase) if supabase else None
    records = list_records(supabase, limit=200) if supabase else []
    return render_template('create/experience_store.html',
                           brands=brands, default_brand=default_brand,
                           records=records, categories=_CATEGORIES,
                           confidentiality=_CONFIDENTIALITY)


@create_bp.route('/experience-store/structure', methods=['POST'])
@login_required
def experience_store_structure():
    """자유 메모 → 구조화 JSON (폼 자동 채움용)."""
    memo = (request.form.get('memo') or '').strip()
    if not memo:
        return jsonify(ok=False, message='메모를 입력해 주세요.')
    from services.config_service import get_config
    from services.experience_store import structure_free_text
    data = structure_free_text(memo, get_config('anthropic_api_key'))
    if not data:
        return jsonify(ok=False, message='구조화에 실패했습니다. 수동으로 입력해 주세요.')
    return jsonify(ok=True, data=data)


@create_bp.route('/experience-store/create', methods=['POST'])
@login_required
def experience_store_create():
    supabase = current_app.supabase
    f = request.form
    title = (f.get('title') or '').strip()
    if not title:
        return jsonify(ok=False, message='제목은 필수입니다.')

    # keywords: 쉼표 구분 → 배열
    kw_raw = (f.get('keywords') or '').strip()
    keywords = [k.strip() for k in kw_raw.split(',') if k.strip()] if kw_raw else []

    # numbers: "항목=값, 항목=값" 또는 JSON
    numbers = {}
    nums_raw = (f.get('numbers') or '').strip()
    if nums_raw:
        import json
        try:
            numbers = json.loads(nums_raw)
        except Exception:
            for pair in nums_raw.split(','):
                if '=' in pair:
                    k, v = pair.split('=', 1)
                    v = v.strip().replace(',', '')
                    numbers[k.strip()] = int(v) if v.lstrip('-').isdigit() else v.strip()

    brand_id = (f.get('brand_id') or '').strip() or None
    row = {
        'brand_id':           brand_id,
        'user_id':            current_user.id,
        'category':           (f.get('category') or '').strip() or None,
        'title':              title,
        'summary':            (f.get('summary') or '').strip() or None,
        'problem':            (f.get('problem') or '').strip() or None,
        'action':             (f.get('action') or '').strip() or None,
        'result':             (f.get('result') or '').strip() or None,
        'numbers_json':       numbers,
        'platform':           (f.get('platform') or '').strip() or None,
        'product':            (f.get('product') or '').strip() or None,
        'keywords':           keywords,
        'confidentiality':    (f.get('confidentiality') or 'anonymized').strip(),
        'usable_for_content': (f.get('usable_for_content') or '1') not in ('0', 'false', ''),
        'evidence_type':      (f.get('evidence_type') or '').strip() or None,
    }
    from services.experience_store import create_record
    rec = create_record(supabase, {k: v for k, v in row.items() if v is not None})
    if not rec:
        return jsonify(ok=False, message='저장에 실패했습니다.')
    return jsonify(ok=True, record=rec)


@create_bp.route('/experience-store/<record_id>', methods=['DELETE'])
@login_required
def experience_store_delete(record_id):
    from services.experience_store import delete_record
    ok = delete_record(current_app.supabase, record_id, user_id=current_user.id)
    return jsonify(ok=ok)
