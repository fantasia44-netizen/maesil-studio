"""상세페이지 기획서 + 초안 제안서 생성"""
import json
import uuid
import logging
from flask import render_template, request, jsonify, redirect, url_for, flash, current_app, send_file
from flask_login import login_required, current_user
from blueprints.create import create_bp
from blueprints.create._base import (
    get_default_brand, get_brand_by_id, run_text_generation, get_accessible_brands
)
from services.tz_utils import now_kst
from io import BytesIO

logger = logging.getLogger(__name__)


# ── 내부 헬퍼 ────────────────────────────────────────────
def _recent_detail_page_creations(supabase, user_id: str, brand_id, limit: int = 5):
    q = supabase.table('creations').select(
        'id, output_data, input_data, created_at'
    ).eq('user_id', user_id).eq('status', 'done').eq(
        'creation_type', 'detail_page_draft'
    )
    if brand_id:
        q = q.eq('brand_id', brand_id)
    try:
        return q.order('created_at', desc=True).limit(limit).execute().data or []
    except Exception:
        return []


def _parse_output(od: dict) -> dict | None:
    if not od:
        return None
    if 'sections' in od:
        return od
    raw = od.get('text', '')
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if 'sections' in parsed:
            return parsed
    except Exception:
        pass
    return {'_legacy_text': raw}


def _get_draft(supabase, draft_id: str) -> dict | None:
    r = supabase.table('creations').select('*').eq(
        'id', draft_id
    ).in_('creation_type', ['detail_page_draft']).execute()
    if not r.data:
        return None
    row = r.data[0]
    if row['user_id'] != current_user.id:
        op = getattr(current_user, 'operator_id', None)
        if not (op and row.get('operator_id') == op):
            return None
    return row


# ── 기획서 페이지 ─────────────────────────────────────────
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
    data     = request.get_json(silent=True) or {}
    brand_id = (data.get('brand_id') or '').strip()
    brand    = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
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
        max_tokens=4096, post_process=_post_process,
    )
    if not result.get('ok'):
        return jsonify(result)

    raw_text = result.get('text', '')
    try:
        parsed = json.loads(raw_text)
        return jsonify(ok=True, creation_id=result['creation_id'], data=parsed)
    except Exception:
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
        ct  = r.get('creation_type', '')
        # 초안: type_name 표시
        label = ''
        if ct == 'detail_page_draft':
            label = od.get('type_name', '')
        parsed = _parse_output(od)
        preview = ''
        if parsed and 'sections' in parsed:
            first = (parsed['sections'] or [{}])[0]
            preview = (first.get('copy') or '')[:60]
        elif parsed and '_legacy_text' in parsed:
            preview = parsed['_legacy_text'][:60]
        result.append({
            'id':           r['id'],
            'type':         ct,
            'label':        label,
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
        'id, user_id, output_data, creation_type'
    ).eq('id', creation_id).eq('status', 'done').in_(
        'creation_type', ['detail_page', 'detail_page_draft']
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
    return jsonify(ok=True, data=parsed, creation_type=row.get('creation_type'))


# ── 초안 제안서 (3타입) ───────────────────────────────────
@create_bp.route('/detail-page/plan', methods=['POST'])
@login_required
def detail_page_plan():
    """Claude 3타입 기획을 Celery 워커에 제출 후 plan_id 반환."""
    from services.point_service import use_points, InsufficientPoints
    from services.config_service import get_config
    from models import POINT_COSTS

    supabase = current_app.supabase
    data     = request.get_json(silent=True) or {}
    brand_id = (data.get('brand_id') or '').strip()
    brand    = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    input_data = {
        'product_name':    (data.get('product_name')    or '').strip(),
        'features':        (data.get('features')        or '').strip(),
        'target_customer': (data.get('target_customer') or '').strip(),
        'price_range':     (data.get('price_range')     or '').strip(),
        'differentiator':  (data.get('differentiator')  or '').strip(),
    }
    if not input_data['product_name'] or not input_data['features']:
        return jsonify(ok=False, message='상품명과 핵심 기능을 입력해 주세요.')

    cost    = POINT_COSTS.get('detail_page_plan', 150)
    plan_id = str(uuid.uuid4())

    try:
        use_points(current_user, 'detail_page_plan', plan_id, cost_override=cost)
    except InsufficientPoints as ip:
        return jsonify(ok=False, error='points', message=str(ip) or '포인트가 부족합니다.')
    except Exception as e:
        logger.error(f'[dp_plan] use_points 오류: {e}')
        return jsonify(ok=False, message='포인트 차감 중 오류가 발생했습니다.')

    try:
        supabase.table('creations').insert({
            'id': plan_id, 'user_id': current_user.id,
            'brand_id': brand['id'], 'creation_type': 'detail_page_plan',
            'input_data': input_data, 'output_data': {}, 'points_used': cost,
            'status': 'generating', 'created_at': now_kst().isoformat(),
        }).execute()
    except Exception as e:
        logger.warning(f'[dp_plan] creation insert 실패: {e}')

    # Celery 워커에 제출
    import os
    from supabase import create_client as _sb_create
    from tasks.detail_page_task import generate_plan

    supabase_url = os.environ.get('SUPABASE_URL', '')
    supabase_key = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY', '')
    anthropic_key = get_config('anthropic_api_key') or os.environ.get('ANTHROPIC_API_KEY', '')

    generate_plan.delay(
        plan_id=plan_id,
        user_id=current_user.id,
        operator_id=getattr(current_user, 'operator_id', None),
        brand=brand,
        input_data=input_data,
        cost=cost,
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        anthropic_api_key=anthropic_key,
    )

    return jsonify(ok=True, plan_id=plan_id, async_mode=True)


@create_bp.route('/detail-page/plan/<plan_id>/status', methods=['GET'])
@login_required
def detail_page_plan_status(plan_id: str):
    """Celery 태스크 완료 여부 폴링."""
    supabase = current_app.supabase
    try:
        r = supabase.table('creations').select(
            'id, status, output_data, user_id'
        ).eq('id', plan_id).single().execute()
        row = r.data
    except Exception:
        return jsonify(ok=False, status='error', message='조회 실패')

    if not row or row.get('user_id') != current_user.id:
        return jsonify(ok=False, status='error', message='권한 없음')

    status = row.get('status', '')
    if status == 'done':
        od = row.get('output_data') or {}
        return jsonify(ok=True, status='done', plans=od.get('plans', []))
    elif status == 'failed':
        od = row.get('output_data') or {}
        return jsonify(ok=False, status='failed',
                       message=od.get('error', 'AI 기획 생성 중 오류가 발생했습니다.'))
    else:
        return jsonify(ok=True, status='generating')


@create_bp.route('/detail-page/draft/init', methods=['POST'])
@login_required
def detail_page_draft_init():
    """선택한 타입 미리보기로 draft 생성 → 카피 생성 Celery 태스크 제출."""
    from services.config_service import get_config
    import os

    supabase = current_app.supabase
    data     = request.get_json(silent=True) or {}
    brand_id = (data.get('brand_id') or '').strip()
    brand    = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    plan_preview = data.get('plan') or {}
    product_name = (data.get('product_name') or '').strip()
    input_data   = data.get('input_data') or {}
    if not plan_preview or not plan_preview.get('sections'):
        return jsonify(ok=False, message='초안 데이터가 없습니다.')

    draft_id = str(uuid.uuid4())
    output_data = {
        'type_name':       plan_preview.get('type_name', ''),
        'product_name':    product_name,
        'appeal_analysis': plan_preview.get('appeal_analysis', {}),
        'copy_status':     'generating',
        'sections':        [
            {**sec, 'copy': '', 'image_url': None, 'img_status': 'pending'}
            for sec in plan_preview.get('sections', [])
        ],
    }
    try:
        supabase.table('creations').insert({
            'id': draft_id, 'user_id': current_user.id,
            'brand_id': brand['id'], 'creation_type': 'detail_page_draft',
            'input_data': {'product_name': product_name, **input_data},
            'output_data': output_data,
            'points_used': 0, 'status': 'generating',
            'created_at': now_kst().isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f'[dp_draft_init] insert 실패: {e}')
        return jsonify(ok=False, message='초안 저장 실패')

    # 카피 생성 Celery 태스크
    from tasks.detail_page_task import generate_copy
    supabase_url = os.environ.get('SUPABASE_URL', '')
    supabase_key = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY', '')
    anthropic_key = get_config('anthropic_api_key') or os.environ.get('ANTHROPIC_API_KEY', '')

    generate_copy.delay(
        draft_id=draft_id,
        brand=brand,
        input_data={**input_data, 'product_name': product_name},
        plan_preview=plan_preview,
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        anthropic_api_key=anthropic_key,
    )

    return jsonify(ok=True, draft_id=draft_id)


@create_bp.route('/detail-page/draft/gen-image', methods=['POST'])
@login_required
def detail_page_draft_gen_image():
    """섹션 1개 스케치 이미지 생성 후 draft 업데이트."""
    from services.point_service import use_points, InsufficientPoints
    from services.imagen_service import generate_image, upload_to_supabase
    from models import POINT_COSTS

    supabase = current_app.supabase
    data     = request.get_json(silent=True) or {}
    draft_id = (data.get('draft_id') or '').strip()
    sec_no   = int(data.get('section_no', 1))

    row = _get_draft(supabase, draft_id)
    if not row:
        return jsonify(ok=False, message='초안을 찾을 수 없습니다.')

    od       = row.get('output_data') or {}
    sections = od.get('sections', [])
    sec      = next((s for s in sections if s.get('no') == sec_no), None)
    if not sec:
        return jsonify(ok=False, message=f'섹션 {sec_no}을 찾을 수 없습니다.')

    image_prompt = (sec.get('image_prompt') or '').strip()
    if not image_prompt:
        # fallback: 섹션명 기반 영문 프롬프트
        name = sec.get('name', 'product')
        image_prompt = f"product detail page scene for {name} section, professional commercial photography, clean background, no text no words"

    cost = POINT_COSTS.get('detail_page_draft_image', 50)
    gen_id = str(uuid.uuid4())

    try:
        use_points(current_user, 'detail_page_draft_image', gen_id, cost_override=cost)

        # FLUX Schnell — 영어 프롬프트이므로 번역 불필요
        # 스케치/레퍼런스 분위기 서픽스 추가
        full_prompt = (
            image_prompt.rstrip('.') +
            ', editorial photography, professional commercial photo, no text, no words, no letters, clean composition'
        )
        image_url, _ = generate_image(full_prompt, engine='flux_preview')

        # Supabase 업로드
        stable_url = upload_to_supabase(image_url, current_user.id, f'dp_draft_{draft_id}_{sec_no}.jpg')

        # output_data 업데이트
        for s in sections:
            if s.get('no') == sec_no:
                s['image_url']  = stable_url
                s['img_status'] = 'done'
                break
        od['sections'] = sections
        supabase.table('creations').update({'output_data': od}).eq('id', draft_id).execute()

        return jsonify(ok=True, image_url=stable_url, section_no=sec_no)

    except InsufficientPoints as ip:
        return jsonify(ok=False, error='points', message=str(ip) or '포인트가 부족합니다.')
    except Exception as e:
        logger.error(f'[dp_draft_gen_image] sec={sec_no} 오류: {e}', exc_info=True)
        return jsonify(ok=False, message='이미지 생성 중 오류가 발생했습니다.')


@create_bp.route('/detail-page/draft/update-copy', methods=['POST'])
@login_required
def detail_page_draft_update_copy():
    """섹션 카피 텍스트 수정."""
    supabase = current_app.supabase
    data     = request.get_json(silent=True) or {}
    draft_id = (data.get('draft_id') or '').strip()
    sec_no   = int(data.get('section_no', 1))
    new_copy = (data.get('copy') or '').strip()

    row = _get_draft(supabase, draft_id)
    if not row:
        return jsonify(ok=False, message='초안을 찾을 수 없습니다.')

    od       = row.get('output_data') or {}
    sections = od.get('sections', [])
    updated  = False
    for s in sections:
        if s.get('no') == sec_no:
            s['copy'] = new_copy
            updated = True
            break
    if not updated:
        return jsonify(ok=False, message='섹션을 찾을 수 없습니다.')

    od['sections'] = sections
    supabase.table('creations').update({'output_data': od}).eq('id', draft_id).execute()
    return jsonify(ok=True)


@create_bp.route('/detail-page/draft/<draft_id>/copy-status', methods=['GET'])
@login_required
def detail_page_draft_copy_status(draft_id: str):
    """카피 생성 완료 여부 폴링."""
    supabase = current_app.supabase
    row = _get_draft(supabase, draft_id)
    if not row:
        return jsonify(ok=False, status='error', message='초안을 찾을 수 없습니다.')
    od = row.get('output_data') or {}
    copy_status = od.get('copy_status', 'generating')
    if copy_status == 'done':
        return jsonify(ok=True, status='done', data=od)
    elif copy_status == 'failed':
        return jsonify(ok=False, status='failed', message=od.get('copy_error', '카피 생성 실패'))
    return jsonify(ok=True, status='generating')


@create_bp.route('/detail-page/draft/<draft_id>', methods=['GET'])
@login_required
def detail_page_draft_load(draft_id: str):
    supabase = current_app.supabase
    row      = _get_draft(supabase, draft_id)
    if not row:
        return jsonify(ok=False, message='초안을 찾을 수 없습니다.')
    return jsonify(ok=True, data=row.get('output_data') or {})


@create_bp.route('/detail-page/draft/<draft_id>/export/<fmt>', methods=['POST'])
@login_required
def detail_page_draft_export(draft_id: str, fmt: str):
    """내보내기 Celery 태스크 제출 → task_id 반환. 워커에서 PNG/PDF 합성 후 Storage 업로드."""
    from tasks.detail_page_task import export_draft
    from services.config_service import get_config

    if fmt not in ('png', 'pdf'):
        return jsonify(ok=False, message=f'지원하지 않는 형식: {fmt}')

    supabase = current_app.supabase
    row = _get_draft(supabase, draft_id)
    if not row:
        return jsonify(ok=False, message='초안을 찾을 수 없습니다.')

    # 이미 생성된 URL이 있으면 즉시 반환
    od = row.get('output_data') or {}
    cached_url = od.get(f'export_{fmt}_url')
    if cached_url:
        return jsonify(ok=True, status='done', url=cached_url)

    supabase_url = current_app.config.get('SUPABASE_URL') or get_config('supabase_url') or ''
    supabase_key = current_app.config.get('SUPABASE_SERVICE_KEY') or get_config('supabase_service_key') or ''

    task = export_draft.delay(draft_id, fmt, supabase_url, supabase_key)
    return jsonify(ok=True, status='queued', task_id=task.id)


@create_bp.route('/detail-page/draft/<draft_id>/export/<fmt>/status', methods=['GET'])
@login_required
def detail_page_draft_export_status(draft_id: str, fmt: str):
    """내보내기 완료 여부 폴링 — done이면 url 반환."""
    supabase = current_app.supabase
    row = _get_draft(supabase, draft_id)
    if not row:
        return jsonify(ok=False, status='error', message='초안을 찾을 수 없습니다.')

    od = row.get('output_data') or {}
    url = od.get(f'export_{fmt}_url')
    if url:
        return jsonify(ok=True, status='done', url=url)
    return jsonify(ok=True, status='generating')
