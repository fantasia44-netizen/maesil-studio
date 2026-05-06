"""상세페이지 빌더 — 블록 기반 시나리오 에디터"""
import uuid
import logging
from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from blueprints.create import create_bp
from blueprints.create._base import get_default_brand, get_accessible_brands, get_brand_by_id
from services.detail_page_templates import list_templates, get_template

logger = logging.getLogger(__name__)

# ── 블록 역할별 텍스트 생성 가이드 ──────────────────────────
_ROLE_GUIDE = {
    'hook':       '강렬하고 공감을 유발하는 헤드라인을 1~3줄로 작성하세요. 질문형이나 강조형 문장을 사용하세요.',
    'empathy':    '고객의 불편함과 감정에 깊이 공감하는 내용을 3~5문장으로 작성하세요. 고객의 언어로 말하세요.',
    'cause':      '문제의 근본 원인을 논리적으로 2~4문장으로 설명하세요.',
    'product':    '제품의 핵심 특징과 혜택을 구체적이고 신뢰감 있게 3~5문장으로 설명하세요.',
    'feature':    '제품의 핵심 기능 하나를 2~3문장으로 상세히 설명하세요.',
    'story':      '진정성 있는 브랜드/개발 스토리를 감성적이고 공감 가는 3~5문장의 내러티브로 작성하세요.',
    'review':     '실제 고객처럼 자연스러운 후기를 ⭐⭐⭐⭐⭐를 포함해 2~3개 작성하세요.',
    'data':       '제품 효과를 구체적인 수치와 데이터로 증명하는 내용을 작성하세요.',
    'expert':     '전문가의 추천 코멘트를 권위 있고 신뢰감 있게 인용부호를 사용해 작성하세요.',
    'lifestyle':  '고객이 꿈꾸는 이상적인 일상과 제품을 연결하는 감성적 묘사를 3~4문장으로 작성하세요.',
    'fomo':       '많은 사람들이 이미 경험하고 있다는 사회적 증거를 수치와 함께 작성하세요.',
    'benefit':    '구매 혜택을 ✓ 기호로 목록 형태로 5~7가지 작성하세요.',
    'before':     '사용 전 불편했던 상황을 공감 가게 2~3문장으로 묘사하세요.',
    'after':      '사용 후 달라진 삶의 변화를 긍정적으로 2~3문장으로 묘사하세요.',
    'comparison': '기존 제품/방법과의 차이점을 명확하게 비교 목록 형태로 작성하세요.',
    'cta':        '구매를 유도하는 강력한 행동 촉구 문구를 1~3가지 버전으로 작성하세요.',
}

_DEFAULT_GUIDE = '해당 섹션에 적합한 내용을 2~4문장으로 작성하세요.'

# ── 이미지 역할별 자동 프롬프트 ─────────────────────────────
_IMAGE_PROMPT_HINTS = {
    'problem':    '고객이 불편함을 겪고 있는 모습. 공감 가는 일상적 상황.',
    'solution':   '문제가 해결된 후 밝고 만족스러운 모습. 긍정적이고 희망적인 분위기.',
    'before':     '사용 전의 힘들거나 불편한 상황. 어둡고 무거운 분위기.',
    'after':      '사용 후 변화된 밝고 행복한 모습. 선명하고 긍정적인 분위기.',
    'product':    '제품을 중심으로 한 깔끔한 상업용 촬영. 스튜디오 조명. 프리미엄 느낌.',
    'lifestyle':  '이상적인 일상 생활 장면. 따뜻하고 자연스러운 라이프스타일 사진.',
    'expert':     '전문가 또는 연구소 환경. 신뢰감 있고 전문적인 분위기.',
    'data':       '깔끔한 인포그래픽 배경 또는 연구 환경. 전문적이고 신뢰감 있는 이미지.',
    'story':      '브랜드의 진정성 있는 스토리를 담은 장면. 따뜻하고 인간적인 분위기.',
    'comparison': '두 가지를 비교하는 명확한 장면. 차이가 한눈에 보이는 구도.',
}


# ════════════════════════════════════════════════════════════
# 페이지 로드
# ════════════════════════════════════════════════════════════

@create_bp.route('/detail-page/builder')
@login_required
def detail_page_builder():
    supabase = current_app.supabase
    brands = get_accessible_brands(supabase)
    default_brand = get_default_brand(supabase)
    if not default_brand:
        flash('먼저 브랜드 프로필을 등록해 주세요.', 'warning')
        return redirect(url_for('main.onboarding'))
    return render_template(
        'create/detail_page_builder.html',
        brands=brands,
        default_brand=default_brand,
        templates=list_templates(),
    )


@create_bp.route('/detail-page/builder/products')
@login_required
def dpb_products():
    """브랜드별 등록 상품 목록 JSON 반환"""
    supabase = current_app.supabase
    brand_id = request.args.get('brand_id', '').strip()
    try:
        q = supabase.table('products').select('id,name,description,features,category') \
            .eq('is_active', True)
        if brand_id:
            q = q.eq('brand_id', brand_id)
        else:
            # 브랜드 미지정 시 접근 가능한 전체 브랜드 상품
            from blueprints.create._base import get_accessible_brands
            brand_ids = [b['id'] for b in get_accessible_brands(supabase)]
            if brand_ids:
                q = q.in_('brand_id', brand_ids)
        result = q.order('created_at', desc=True).limit(50).execute()
        products = result.data or []
        # features가 list면 join
        for p in products:
            if isinstance(p.get('features'), list):
                p['features_text'] = ', '.join(p['features'])
            else:
                p['features_text'] = p.get('features') or ''
        return jsonify(ok=True, products=products)
    except Exception as e:
        logger.error(f'[DPB] products error: {e}')
        return jsonify(ok=False, products=[])


@create_bp.route('/detail-page/builder/template/<template_id>')
@login_required
def detail_page_builder_template(template_id):
    """템플릿 블록 구조 JSON 반환"""
    tpl = get_template(template_id)
    if not tpl:
        return jsonify(ok=False, message='템플릿을 찾을 수 없습니다.')
    return jsonify(ok=True, template=tpl)


# ════════════════════════════════════════════════════════════
# 2단계: AI 텍스트 생성
# ════════════════════════════════════════════════════════════

@create_bp.route('/detail-page/builder/gen-text', methods=['POST'])
@login_required
def dpb_gen_text():
    """블록 텍스트 AI 생성 (30P/블록)"""
    supabase = current_app.supabase
    data = request.get_json(silent=True) or {}

    brand_id       = data.get('brand_id', '')
    template_id    = data.get('template_id', '')
    block_role     = data.get('block_role', '')
    block_label    = data.get('block_label', '')
    product_name   = data.get('product_name', '').strip()
    product_features = data.get('product_features', '').strip()
    context_summary  = data.get('context_summary', '')   # 다른 블록 내용 요약

    if not product_name:
        return jsonify(ok=False, message='상품명을 먼저 입력해 주세요.')

    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    tpl = get_template(template_id) if template_id else None
    tpl_name      = tpl['name']      if tpl else '상세페이지'
    tpl_narrative = tpl['narrative'] if tpl else '고객 중심의 상세페이지'

    # 포인트 차감
    from services.point_service import use_points, InsufficientPoints
    creation_id = str(uuid.uuid4())
    try:
        use_points(current_user, 'dp_block_text', creation_id,
                   cost_override=30, note_override=f'상세페이지 블록 텍스트 ({block_label})')
    except InsufficientPoints as e:
        return jsonify(ok=False, error='points', message=str(e) or '포인트가 부족합니다.')

    # 프롬프트 구성
    from services.claude_service import SYSTEM_BASE, build_brand_context, generate_text
    brand_ctx = build_brand_context(brand)
    guide     = _ROLE_GUIDE.get(block_role, _DEFAULT_GUIDE)

    system = f"""{SYSTEM_BASE}

[브랜드 컨텍스트]
{brand_ctx}

[상세페이지 서사 구조: {tpl_name}]
{tpl_narrative}"""

    product_ctx = f'- 상품명: {product_name}'
    if product_features:
        product_ctx += f'\n- 핵심 특징/소구포인트: {product_features}'

    ctx_part = f'\n\n[페이지 전체 흐름 참고]\n{context_summary}' if context_summary else ''

    user = f"""상세페이지의 [{block_label}] 섹션 내용을 작성해 주세요.

[상품 정보]
{product_ctx}
{ctx_part}

[작성 가이드]
{guide}

주의사항:
- 마크다운 기호(###, **, __ 등) 없이 순수 텍스트로만 작성하세요.
- 자연스럽고 고객이 공감할 수 있는 언어를 사용하세요.
- 브랜드 톤앤매너를 유지하세요."""

    try:
        text = generate_text(system, user, max_tokens=800, model='claude-haiku-4-5-20251001')
        return jsonify(ok=True, text=text.strip())
    except Exception as e:
        logger.error(f'[DPB] gen-text error: {e}')
        return jsonify(ok=False, message='AI 생성 중 오류가 발생했습니다.')


# ════════════════════════════════════════════════════════════
# 2단계: FLUX 이미지 생성
# ════════════════════════════════════════════════════════════

@create_bp.route('/detail-page/builder/gen-image', methods=['POST'])
@login_required
def dpb_gen_image():
    """블록 이미지 FLUX 생성 (50P)"""
    supabase = current_app.supabase
    data = request.get_json(silent=True) or {}

    brand_id        = data.get('brand_id', '')
    block_role      = data.get('block_role', 'product')
    block_label     = data.get('block_label', '이미지')
    image_prompt    = data.get('image_prompt', '').strip()
    product_name    = data.get('product_name', '').strip()
    product_features= data.get('product_features', '').strip()
    engine          = data.get('engine', 'flux_preview')   # flux_preview | flux_standard

    # 이미지 프롬프트 자동 생성 (미입력 시)
    if not image_prompt:
        hint  = _IMAGE_PROMPT_HINTS.get(block_role, '제품 관련 이미지')
        parts = [hint]
        if product_name:    parts.append(product_name)
        if product_features: parts.append(product_features)
        image_prompt = ', '.join(parts)

    # 포인트 비용 결정
    cost = 50 if engine == 'flux_preview' else 300

    # 포인트 차감
    from services.point_service import use_points, InsufficientPoints
    creation_id = str(uuid.uuid4())
    try:
        use_points(current_user, 'dp_block_image', creation_id,
                   cost_override=cost, note_override=f'상세페이지 이미지 ({block_label})')
    except InsufficientPoints as e:
        return jsonify(ok=False, error='points', message=str(e) or '포인트가 부족합니다.')

    # FLUX 생성
    try:
        from services.imagen_service import generate_image, upload_to_supabase
        image_url, _ = generate_image(image_prompt, engine=engine, size='1024x1024')

        # Supabase Storage에 업로드 (안정적인 URL 확보)
        stable_url = upload_to_supabase(image_url, current_user.id, f'dpb_{block_role}.jpg')
        return jsonify(ok=True, image_url=stable_url, prompt_used=image_prompt)
    except Exception as e:
        logger.error(f'[DPB] gen-image error: {e}')
        return jsonify(ok=False, message=f'이미지 생성 중 오류가 발생했습니다: {str(e)[:80]}')


# ════════════════════════════════════════════════════════════
# 2단계: 이미지 직접 업로드
# ════════════════════════════════════════════════════════════

@create_bp.route('/detail-page/builder/upload-image', methods=['POST'])
@login_required
def dpb_upload_image():
    """이미지 직접 업로드 → Supabase Storage (무료)"""
    if 'file' not in request.files:
        return jsonify(ok=False, message='파일이 없습니다.')

    f = request.files['file']
    if not f.filename:
        return jsonify(ok=False, message='파일명이 없습니다.')

    allowed = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
    mime = f.content_type or 'image/jpeg'
    if mime not in allowed:
        return jsonify(ok=False, message='지원하지 않는 파일 형식입니다. (JPG/PNG/WEBP/GIF)')

    try:
        raw   = f.read()
        ext   = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'jpg'
        fname = f'dpb_{uuid.uuid4().hex[:8]}.{ext}'

        from services.imagen_service import upload_to_supabase
        import base64
        b64 = base64.b64encode(raw).decode()
        data_url = f'data:{mime};base64,{b64}'
        url = upload_to_supabase(data_url, current_user.id, fname)
        return jsonify(ok=True, image_url=url)
    except Exception as e:
        logger.error(f'[DPB] upload-image error: {e}')
        return jsonify(ok=False, message='업로드 중 오류가 발생했습니다.')


# ════════════════════════════════════════════════════════════
# 3단계: 배경 교체 (Bria AI)
# ════════════════════════════════════════════════════════════

@create_bp.route('/detail-page/builder/bg-replace', methods=['POST'])
@login_required
def dpb_bg_replace():
    """제품 이미지 배경 교체 — Bria AI (80P).

    기존 이미지(image_url)의 배경만 교체하고 제품·로고는 보존.
    bg_prompt: 새 배경 설명 (한국어 OK — 내부에서 영어 번역)
    """
    data = request.get_json(silent=True) or {}
    image_url = data.get('image_url', '').strip()
    bg_prompt = data.get('bg_prompt', '').strip()
    block_label = data.get('block_label', '이미지')

    if not image_url:
        return jsonify(ok=False, message='기준 이미지가 없습니다. 먼저 이미지를 업로드하거나 생성해 주세요.')
    if not bg_prompt:
        return jsonify(ok=False, message='새 배경 설명을 입력해 주세요.')

    # 포인트 차감
    from services.point_service import use_points, InsufficientPoints
    creation_id = str(uuid.uuid4())
    try:
        use_points(current_user, 'dp_bg_replace', creation_id,
                   cost_override=80, note_override=f'상세페이지 배경 교체 ({block_label})')
    except InsufficientPoints as e:
        return jsonify(ok=False, error='points', message=str(e) or '포인트가 부족합니다.')

    try:
        from services.imagen_service import replace_background, upload_to_supabase, _translate_prompt, _has_korean

        # 한국어 배경 설명 → 영어 번역
        bg_en = _translate_prompt(bg_prompt) if _has_korean(bg_prompt) else bg_prompt

        new_url = replace_background(image_url, bg_en)
        stable_url = upload_to_supabase(new_url, current_user.id, f'dpb_bg_{uuid.uuid4().hex[:6]}.jpg')
        return jsonify(ok=True, image_url=stable_url)
    except Exception as e:
        logger.error(f'[DPB] bg-replace error: {e}')
        return jsonify(ok=False, message=f'배경 교체 중 오류가 발생했습니다: {str(e)[:80]}')


# ════════════════════════════════════════════════════════════
# 3단계: FLUX 배경 + PIL 텍스트 오버레이
# ════════════════════════════════════════════════════════════

@create_bp.route('/detail-page/builder/flux-text', methods=['POST'])
@login_required
def dpb_flux_text():
    """FLUX 배경 이미지 + PIL 한글 텍스트 합성 (300P).

    bg_prompt: 배경 이미지 설명 (한국어 OK)
    texts:     표시할 한글 문구 목록 (최대 3줄)
    brand_color: 텍스트 배너 색상 (기본 #4b5cde)
    font_color:  텍스트 색상 (기본 #ffffff)
    """
    data = request.get_json(silent=True) or {}
    brand_id    = data.get('brand_id', '')
    bg_prompt   = data.get('bg_prompt', '').strip()
    texts       = [t.strip() for t in data.get('texts', []) if str(t).strip()]
    brand_color = data.get('brand_color', '#4b5cde')
    font_color  = data.get('font_color', '#ffffff')
    block_label = data.get('block_label', '이미지')

    if not bg_prompt:
        return jsonify(ok=False, message='배경 이미지 설명을 입력해 주세요.')
    if not texts:
        return jsonify(ok=False, message='표시할 텍스트를 입력해 주세요.')

    # 브랜드 색상 우선 적용
    supabase = current_app.supabase
    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if brand and brand.get('primary_color'):
        brand_color = brand['primary_color']

    # 포인트 차감
    from services.point_service import use_points, InsufficientPoints
    creation_id = str(uuid.uuid4())
    try:
        use_points(current_user, 'dp_flux_text', creation_id,
                   cost_override=300, note_override=f'상세페이지 텍스트 조합 이미지 ({block_label})')
    except InsufficientPoints as e:
        return jsonify(ok=False, error='points', message=str(e) or '포인트가 부족합니다.')

    try:
        from services.imagen_service import generate_card_news, upload_to_supabase
        data_url, prompt_used = generate_card_news(
            texts=texts[:3],
            background_prompt=bg_prompt,
            brand_color=brand_color,
            font_color=font_color,
        )
        stable_url = upload_to_supabase(data_url, current_user.id, f'dpb_txt_{uuid.uuid4().hex[:6]}.jpg')
        return jsonify(ok=True, image_url=stable_url, prompt_used=prompt_used)
    except Exception as e:
        logger.error(f'[DPB] flux-text error: {e}')
        return jsonify(ok=False, message=f'텍스트 조합 이미지 생성 중 오류가 발생했습니다: {str(e)[:80]}')
