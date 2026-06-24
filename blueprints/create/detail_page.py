"""상세페이지 소구포인트 기획서 생성"""
import json
import logging
from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from blueprints.create import create_bp
from blueprints.create._base import (
    get_default_brand, get_brand_by_id, run_text_generation, get_accessible_brands
)

logger = logging.getLogger(__name__)


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


def _parse_output(od: dict) -> dict | None:
    """output_data에서 기획서 구조를 꺼냄. 구형 텍스트 포맷도 처리."""
    if not od:
        return None
    # 신형: sections 키가 있으면 바로 반환
    if 'sections' in od:
        return od
    # 구형: text 키에 JSON 문자열 저장
    raw = od.get('text', '')
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if 'sections' in parsed:
            return parsed
    except Exception:
        pass
    # 아주 구형: 순수 텍스트 → 하위 호환 래핑
    return {'_legacy_text': raw}


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
        'product_name':    (data.get('product_name')    or '').strip(),
        'features':        (data.get('features')        or '').strip(),
        'target_customer': (data.get('target_customer') or '').strip(),
        'price_range':     (data.get('price_range')     or '').strip(),
        'differentiator':  (data.get('differentiator')  or '').strip(),
    }

    from services.prompts.detail_page import build_prompt
    system, user = build_prompt(brand, input_data)

    def _post_process(raw: str) -> str:
        """JSON 파싱 검증 후 그대로 반환 (파싱 불가면 원문 유지)."""
        try:
            cleaned = raw.strip()
            if cleaned.startswith('```'):
                cleaned = cleaned.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
            json.loads(cleaned)
            return cleaned
        except Exception:
            return raw

    result = run_text_generation(
        'detail_page', brand, input_data, system, user,
        max_tokens=4096,
        post_process=_post_process,
    )
    if not result.get('ok'):
        return jsonify(result)

    # JSON 파싱해서 구조화된 데이터로 응답
    raw_text = result.get('text', '')
    try:
        parsed = json.loads(raw_text)
        return jsonify(ok=True, creation_id=result['creation_id'], data=parsed)
    except Exception:
        # JSON 파싱 실패 시 legacy 텍스트로 반환
        return jsonify(ok=True, creation_id=result['creation_id'], data={'_legacy_text': raw_text})


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
        parsed = _parse_output(od)
        # 첫 섹션 카피 일부를 미리보기로
        preview = ''
        if parsed and 'sections' in parsed:
            first = parsed['sections'][0] if parsed['sections'] else {}
            preview = (first.get('copy') or '')[:80]
        elif parsed and '_legacy_text' in parsed:
            preview = parsed['_legacy_text'][:80]
        result.append({
            'id':           r['id'],
            'product_name': (inp.get('product_name') or '제목 없음')[:40],
            'text_preview': preview,
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
    parsed = _parse_output(row.get('output_data') or {})
    if not parsed:
        return jsonify(ok=False, message='결과 없음')
    return jsonify(ok=True, data=parsed)
