"""상품 관리 + 일괄 콘텐츠 생성"""
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from services.tz_utils import now_kst
from blueprints.create._base import get_accessible_brands, get_default_brand, run_text_generation

logger = logging.getLogger(__name__)
product_bp = Blueprint('product', __name__)


def _get_accessible_products(supabase):
    """OR 매칭 — operator_id 또는 user_id 둘 중 하나라도 본인이면 노출.

    INSERT 시 user_id 는 항상 채우고 operator_id 는 있을 때만 채우는 정책과
    일치. operator 모드에서 operator_id 만 필터하면 본인 user_id 명의의
    legacy row 가 누락됨.
    """
    base = supabase.table('products').select('*, brand_profiles(name)') \
        .eq('is_active', True)
    op_id = current_user.operator_id
    if op_id:
        # operator_id 매칭 OR user_id 매칭 (PostgREST .or_ 절)
        result = base.or_(
            f'operator_id.eq.{op_id},user_id.eq.{current_user.id}'
        ).order('created_at', desc=True).execute()
    else:
        result = base.eq('user_id', current_user.id) \
            .order('created_at', desc=True).execute()
    return result.data or []


def _get_product(supabase, product_id: str):
    result = supabase.table('products').select('*').eq('id', product_id).execute()
    if not result.data:
        return None
    p = result.data[0]
    # 슈퍼어드민은 운영자 지원/정정용으로 모든 상품 접근 가능
    if current_user.is_superadmin:
        return p
    # OR 매칭: operator 매칭 OR 본인 user_id 매칭 (_save_product INSERT 정책과 일치)
    if (current_user.operator_id
            and p.get('operator_id') == current_user.operator_id):
        return p
    if p.get('user_id') == str(current_user.id):
        return p
    return None


def _parse_features(text: str) -> list:
    return [f.strip() for f in text.replace('\n', ',').split(',') if f.strip()]


# ── 상품 목록 ───────────────────────────────────────────
@product_bp.route('/')
@login_required
def index():
    supabase = current_app.supabase
    products = _get_accessible_products(supabase)
    brands = get_accessible_brands(supabase)
    return render_template('product/index.html', products=products, brands=brands)


# ── 상품 등록 ───────────────────────────────────────────
@product_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    supabase = current_app.supabase
    brands = get_accessible_brands(supabase)

    if request.method == 'GET':
        return render_template('product/edit.html', product=None, brands=brands)

    return _save_product(supabase, brands, product_id=None)


# ── 상품 수정 ───────────────────────────────────────────
@product_bp.route('/<product_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(product_id):
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        flash('상품을 찾을 수 없습니다.', 'warning')
        return redirect(url_for('product.index'))
    brands = get_accessible_brands(supabase)

    if request.method == 'GET':
        return render_template('product/edit.html', product=product, brands=brands)
    return _save_product(supabase, brands, product_id=product_id)


def _save_product(supabase, brands, product_id):
    brand_id = request.form.get('brand_id') or None
    # brand_id 없으면 기본 브랜드
    if not brand_id and brands:
        default = next((b for b in brands if b.get('is_default')), brands[0] if brands else None)
        brand_id = default['id'] if default else None

    data = {
        'brand_id': brand_id,
        'name': request.form.get('name', '').strip(),
        'category': request.form.get('category', '').strip(),
        'price': request.form.get('price', type=int),
        'product_url': request.form.get('product_url', '').strip(),
        'description': request.form.get('description', '').strip(),
        'features': _parse_features(request.form.get('features', '')),
        'updated_at': now_kst().isoformat(),
    }

    if not data['name']:
        flash('상품명을 입력하세요.', 'warning')
        return render_template('product/edit.html', product=data, brands=brands)

    try:
        if product_id:
            supabase.table('products').update(data).eq('id', product_id).execute()
            flash('상품이 수정되었습니다.', 'success')
        else:
            data['user_id'] = current_user.id
            data['is_active'] = True
            data['created_at'] = now_kst().isoformat()
            if current_user.operator_id:
                data['operator_id'] = current_user.operator_id
            supabase.table('products').insert(data).execute()
            flash('상품이 등록되었습니다.', 'success')
        return redirect(url_for('product.index'))
    except Exception as e:
        logger.error(f'[PRODUCT] save error: {e}')
        flash('저장 중 오류가 발생했습니다.', 'danger')
        return render_template('product/edit.html', product=data, brands=brands)


# ── 상품 상세 + 생성 허브 ────────────────────────────────
@product_bp.route('/<product_id>')
@login_required
def detail(product_id):
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        flash('상품을 찾을 수 없습니다.', 'warning')
        return redirect(url_for('product.index'))

    brand = None
    if product.get('brand_id'):
        r = supabase.table('brand_profiles').select('*').eq('id', product['brand_id']).execute()
        brand = r.data[0] if r.data else None
    if not brand:
        brand = get_default_brand(supabase)

    # 최근 생성 이력
    creations = supabase.table('creations').select(
        'id, creation_type, status, created_at, output_data'
    ).eq('user_id', current_user.id).eq(
        'input_data->>product_id', product_id
    ).order('created_at', desc=True).limit(20).execute()

    return render_template('product/detail.html',
                           product=product,
                           brand=brand,
                           creations=creations.data or [])


# ── 일괄 생성 API ────────────────────────────────────────
@product_bp.route('/<product_id>/generate-all', methods=['POST'])
@login_required
def generate_all(product_id):
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    brand = None
    if product.get('brand_id'):
        r = supabase.table('brand_profiles').select('*').eq('id', product['brand_id']).execute()
        brand = r.data[0] if r.data else None
    if not brand:
        brand = get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    types = request.json.get('types', ['blog', 'instagram', 'detail_page', 'ad_copy'])
    input_data = _product_to_input(product)

    results = {}
    for creation_type in types:
        try:
            system, user_prompt = _build_product_prompt(creation_type, brand, product)
            r = run_text_generation(creation_type, brand, input_data, system, user_prompt)
            results[creation_type] = r
        except Exception as e:
            logger.error(f'[PRODUCT] generate {creation_type} error: {e}')
            results[creation_type] = {'ok': False, 'message': str(e)}

    all_ok = all(v.get('ok') for v in results.values())
    return jsonify(ok=all_ok, results=results)


# ── 단일 생성 API ────────────────────────────────────────
@product_bp.route('/<product_id>/generate', methods=['POST'])
@login_required
def generate_one(product_id):
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    creation_type = request.json.get('type', 'blog')

    brand = None
    if product.get('brand_id'):
        r = supabase.table('brand_profiles').select('*').eq('id', product['brand_id']).execute()
        brand = r.data[0] if r.data else None
    if not brand:
        brand = get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    input_data = _product_to_input(product)
    system, user_prompt = _build_product_prompt(creation_type, brand, product)
    result = run_text_generation(creation_type, brand, input_data, system, user_prompt)
    return jsonify(result)


# ── 상품 이미지: URL 가져오기 ────────────────────────────
@product_bp.route('/<product_id>/import-url', methods=['POST'])
@login_required
def import_url(product_id):
    product = _get_product(current_app.supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    url = (request.json or {}).get('url', '').strip()
    if not url:
        return jsonify(ok=False, message='URL을 입력하세요.')

    try:
        from services.url_importer import fetch_product_info, detect_platform
        info = fetch_product_info(url)
        return jsonify(ok=True, **info)
    except Exception as e:
        logger.error(f'[PRODUCT] import_url error: {e}')
        return jsonify(ok=False, message=str(e))


# ── 상품 이미지: 파일 업로드 ─────────────────────────────
@product_bp.route('/<product_id>/upload-image', methods=['POST'])
@login_required
def upload_image(product_id):
    product = _get_product(current_app.supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    file = request.files.get('file')
    if not file:
        return jsonify(ok=False, message='파일을 선택하세요.')

    import uuid
    try:
        image_bytes = file.read()
        filename = f'product_{product_id[:8]}_{uuid.uuid4().hex[:8]}.jpg'
        path = f'{current_user.id}/products/{filename}'

        supabase = current_app.supabase
        mime = file.content_type or 'image/jpeg'
        supabase.storage.from_('creations').upload(path, image_bytes, {'content-type': mime})
        public_url = supabase.storage.from_('creations').get_public_url(path)
        return jsonify(ok=True, image_url=public_url)
    except Exception as e:
        logger.error(f'[PRODUCT] upload_image error: {e}')
        return jsonify(ok=False, message=str(e))


# ── 상품 이미지 목록 저장 ────────────────────────────────
@product_bp.route('/<product_id>/save-images', methods=['POST'])
@login_required
def save_images(product_id):
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    images = (request.json or {}).get('images', [])
    try:
        supabase.table('products').update({
            'images': images,
            'updated_at': now_kst().isoformat(),
        }).eq('id', product_id).execute()
        return jsonify(ok=True, message=f'{len(images)}개 이미지가 저장되었습니다.')
    except Exception as e:
        logger.error(f'[PRODUCT] save_images error: {e}')
        return jsonify(ok=False, message=str(e))


# ── 인사이트 이미지 가져오기 ──────────────────────────────
@product_bp.route('/<product_id>/insight-images', methods=['POST'])
@login_required
def insight_images(product_id):
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    source_ref = product.get('source_ref')
    if not source_ref:
        return jsonify(ok=False, message='매실 인사이트에서 가져온 상품이 아닙니다.')

    try:
        from services.maesil_insight_connection import get_client_for_user
        client = get_client_for_user(current_user.id)
        if not client:
            return jsonify(ok=False, message='매실 인사이트 연결이 없습니다. 연동 설정을 확인하세요.')

        detail = client.get_product(source_ref)

        from blueprints.integrations import _collect_all_image_urls
        ext_images = _collect_all_image_urls(detail)
        logger.info(f'[PRODUCT] insight_images: {source_ref} 이미지 {len(ext_images)}장 수집')

        if not ext_images:
            return jsonify(ok=False, message='인사이트에서 이미지를 찾을 수 없습니다.')

        # Supabase Storage에 다운로드 후 저장
        from blueprints.integrations import _download_and_store_images
        source_ref = product.get('source_ref', product_id[:8])
        images = _download_and_store_images(supabase, current_user.id, source_ref, ext_images)

        # 기존 이미지와 합치기
        existing = product.get('images') or []
        merged = existing + [img for img in images if img not in existing]
        supabase.table('products').update({
            'images': merged,
            'updated_at': now_kst().isoformat(),
        }).eq('id', product_id).execute()

        return jsonify(ok=True, images=images, message=f'{len(images)}개 이미지를 가져왔습니다.')
    except Exception as e:
        logger.error(f'[PRODUCT] insight_images error: {e}')
        return jsonify(ok=False, message=str(e))


# ── 상품 삭제 ───────────────────────────────────────────
@product_bp.route('/<product_id>/delete', methods=['POST'])
@login_required
def delete(product_id):
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        flash('상품을 찾을 수 없습니다.', 'warning')
        return redirect(url_for('product.index'))
    try:
        supabase.table('products').update({'is_active': False}).eq('id', product_id).execute()
        flash('상품이 삭제되었습니다.', 'info')
    except Exception as e:
        logger.error(f'[PRODUCT] delete error: {e}')
        flash('오류가 발생했습니다.', 'danger')
    return redirect(url_for('product.index'))


# ── 콘텐츠 스튜디오 ─────────────────────────────────────

STUDIO_TYPE_LABELS = {
    'blog': '블로그 포스트',
    'instagram': '인스타그램 게시물',
    'detail_page': '상품 상세페이지',
    'ad_copy': '광고 카피',
}

STUDIO_POINT_COSTS = {
    'blog': 80, 'instagram': 30, 'detail_page': 150, 'ad_copy': 60,
}

IMG_GEN_COST = 30  # 이미지 1장당 포인트


def _studio_product_ctx(product: dict) -> str:
    features_str = '\n'.join(f'- {f}' for f in (product.get('features') or []))
    price_str = f"{product['price']:,}원" if product.get('price') else '미설정'
    return (
        f"상품명: {product['name']}\n"
        f"카테고리: {product.get('category', '')}\n"
        f"가격: {price_str}\n"
        f"핵심 특징:\n{features_str}\n"
        f"상품 설명: {product.get('description', '')}"
    )


@product_bp.route('/<product_id>/studio/angles', methods=['POST'])
@login_required
def studio_angles(product_id):
    """Step 1: 소구포인트 시안 3개 생성 (무료)."""
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    content_type = (request.json or {}).get('content_type', 'blog')
    type_label = STUDIO_TYPE_LABELS.get(content_type, content_type)

    brand = None
    if product.get('brand_id'):
        r = supabase.table('brand_profiles').select('*').eq('id', product['brand_id']).execute()
        brand = r.data[0] if r.data else None
    if not brand:
        brand = get_default_brand(supabase)

    from services.claude_service import generate_text, build_brand_context
    brand_ctx = build_brand_context(brand) if brand else ''
    product_ctx = _studio_product_ctx(product)

    system = (
        '당신은 한국 마케팅 전문가입니다. 상품 정보를 분석해 콘텐츠 소구포인트 시안을 JSON으로 제안합니다. '
        '반드시 유효한 JSON 배열만 출력하세요. 마크다운 코드블록 없이 순수 JSON만 출력합니다.'
    )
    user = f"""[브랜드 정보]
{brand_ctx}

[상품 정보]
{product_ctx}

위 상품의 {type_label} 콘텐츠 소구포인트 시안 3가지를 제안하세요.
각 시안은 완전히 다른 접근 방식을 사용해야 합니다 (감성/기능/혜택 등 다양하게).

JSON 배열로만 응답하세요:
[
  {{
    "id": "A",
    "title": "시안 제목 (15자 이내)",
    "hook": "핵심 후킹 문구 — 독자 눈길을 잡는 첫 문장 (35자 이내)",
    "target": "주 타겟 독자 (예: 30대 워킹맘, 건강 관심 20대 등)",
    "tone": "글 톤앤매너 (예: 따뜻하고 감성적, 전문적/신뢰감, 친근하고 유머러스)",
    "approach": "이 시안의 핵심 접근 방식 한 줄 설명",
    "key_points": ["포인트1", "포인트2", "포인트3"]
  }}
]"""

    try:
        import json, re
        raw = generate_text(system, user, max_tokens=1500)
        match = re.search(r'\[[\s\S]*\]', raw)
        if not match:
            raise ValueError('JSON 배열 없음')
        angles = json.loads(match.group())
        return jsonify(ok=True, angles=angles, content_type=content_type)
    except Exception as e:
        logger.error(f'[STUDIO] angles error: {e}')
        return jsonify(ok=False, message=f'소구포인트 생성 실패: {e}')


@product_bp.route('/<product_id>/studio/content', methods=['POST'])
@login_required
def studio_content(product_id):
    """Step 2: 선택된 시안으로 콘텐츠 초안 생성 (포인트 차감)."""
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    body = request.json or {}
    content_type = body.get('content_type', 'blog')
    angle = body.get('angle', {})

    brand = None
    if product.get('brand_id'):
        r = supabase.table('brand_profiles').select('*').eq('id', product['brand_id']).execute()
        brand = r.data[0] if r.data else None
    if not brand:
        brand = get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    from services.claude_service import build_brand_context, SYSTEM_BASE
    brand_ctx = build_brand_context(brand)
    product_ctx = _studio_product_ctx(product)
    type_label = STUDIO_TYPE_LABELS.get(content_type, content_type)

    angle_ctx = ''
    if angle:
        angle_ctx = f"""
[선택된 소구포인트 시안]
- 시안 제목: {angle.get('title', '')}
- 핵심 후킹 문구: {angle.get('hook', '')}
- 타겟 독자: {angle.get('target', '')}
- 톤앤매너: {angle.get('tone', '')}
- 접근 방식: {angle.get('approach', '')}
- 핵심 포인트: {', '.join(angle.get('key_points', []))}

위 소구포인트 시안 방향에 맞게 콘텐츠를 작성해 주세요."""

    system = f"{SYSTEM_BASE}\n\n[브랜드 정보]\n{brand_ctx}"

    content_instructions = {
        'blog': '- 제목 3가지 (클릭률 최적화)\n- SEO 최적화 본문 2,000자 내외\n- 마무리 CTA 문구\n- 해시태그 10개',
        'instagram': '- 캡션 3가지 버전 (짧·중·긴)\n- 각 버전별 해시태그 30개',
        'detail_page': '- 상단 훅 문구 (3초 안에 구매욕 자극)\n- 핵심 기능 설명 (기능별 카피)\n- 고객 후기 포맷 3개\n- CTA 문구 5종\n- FAQ 5개',
        'ad_copy': '- 헤드라인 5종 (감성/혜택/비교/호기심/긴급 소구)\n- 각 헤드라인별 본문 1~2줄\n- CTA 문구',
    }
    instructions = content_instructions.get(content_type, f'{type_label} 콘텐츠를 작성해 주세요.')

    user = f"""[상품 정보]
{product_ctx}
{angle_ctx}

{type_label}를 작성해 주세요:
{instructions}"""

    cost = STUDIO_POINT_COSTS.get(content_type, 30)
    input_data = _product_to_input(product)
    input_data['studio_angle'] = angle.get('title', '')
    input_data['studio_content_type'] = content_type

    result = run_text_generation(
        content_type, brand, input_data, system, user,
        point_cost=cost,
        ledger_note=f'스튜디오 {type_label} [{angle.get("title", "")}]',
        extra_creation_fields={'product_id': product_id},
    )
    return jsonify(result)


@product_bp.route('/<product_id>/studio/image-prompts', methods=['POST'])
@login_required
def studio_image_prompts(product_id):
    """Step 3: 글 내용 기반 이미지 프롬프트 생성 (무료)."""
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    body = request.json or {}
    content_type = body.get('content_type', 'blog')
    content_text = body.get('content', '')[:3000]  # 긴 글은 앞부분만
    angle = body.get('angle', {})

    from services.claude_service import generate_text
    product_ctx = _studio_product_ctx(product)
    has_product_images = bool(product.get('images'))
    img_note = (
        '※ 상품 실물 이미지가 있으므로, 배경/분위기/라이프스타일 합성에 적합한 프롬프트를 작성하세요.'
        if has_product_images else
        '※ 상품 이미지가 없으므로, 상품이 자연스럽게 등장하는 라이프스타일 장면을 묘사하세요.'
    )

    type_image_roles = {
        'blog': ['메인 썸네일', '본문 삽입 이미지 1', '본문 삽입 이미지 2'],
        'instagram': ['피드 대표 이미지', '카드뉴스 1', '카드뉴스 2'],
        'detail_page': ['상단 히어로 이미지', '기능 설명 이미지', '라이프스타일 이미지'],
        'ad_copy': ['광고 메인 비주얼', '배너 이미지', '서브 비주얼'],
    }
    roles = type_image_roles.get(content_type, ['이미지 1', '이미지 2', '이미지 3'])

    system = (
        'You are a professional image prompt engineer for Korean e-commerce brands. '
        'Generate FLUX image generation prompts in English. '
        'Output only valid JSON array, no markdown code blocks.'
    )
    user = f"""Product info:
{product_ctx}

Content type: {STUDIO_TYPE_LABELS.get(content_type, content_type)}
Angle/concept: {angle.get('title', '')} — {angle.get('approach', '')}
Tone: {angle.get('tone', '')}
Target audience: {angle.get('target', '')}

Content draft (excerpt):
{content_text[:800]}

{img_note}

Generate exactly 3 image prompts for these roles: {roles}

JSON array format:
[
  {{
    "role": "{roles[0]}",
    "prompt": "detailed English image generation prompt optimized for FLUX, photorealistic style, Korean market aesthetic, high quality commercial photography",
    "aspect": "16:9 or 1:1 or 4:5",
    "style_note": "간단한 스타일 설명 (한국어)"
  }}
]"""

    try:
        import json, re
        raw = generate_text(system, user, max_tokens=1200)
        match = re.search(r'\[[\s\S]*\]', raw)
        if not match:
            raise ValueError('JSON 배열 없음')
        prompts = json.loads(match.group())
        return jsonify(ok=True, prompts=prompts, has_product_images=has_product_images)
    except Exception as e:
        logger.error(f'[STUDIO] image_prompts error: {e}')
        return jsonify(ok=False, message=f'이미지 프롬프트 생성 실패: {e}')


@product_bp.route('/<product_id>/studio/generate-images', methods=['POST'])
@login_required
def studio_generate_images(product_id):
    """Step 3: fal.ai FLUX로 이미지 생성 (장당 포인트 차감)."""
    supabase = current_app.supabase
    product = _get_product(supabase, product_id)
    if not product:
        return jsonify(ok=False, message='상품을 찾을 수 없습니다.')

    body = request.json or {}
    prompt_items = body.get('prompts', [])  # [{"role": ..., "prompt": ..., "aspect": ...}]
    if not prompt_items:
        return jsonify(ok=False, message='프롬프트가 없습니다.')

    from services.point_service import use_points, InsufficientPoints
    from services.config_service import get_config
    import requests as req_lib, uuid

    api_key = get_config('fal_api_key')
    if not api_key:
        return jsonify(ok=False, message='fal.ai API 키가 설정되지 않았습니다.')

    total_cost = IMG_GEN_COST * len(prompt_items)
    try:
        use_points(current_user.id, 'image_generation', str(uuid.uuid4()),
                   cost_override=total_cost,
                   note_override=f'스튜디오 이미지 {len(prompt_items)}장')
    except InsufficientPoints as ip:
        return jsonify(ok=False, error='points', message=str(ip))

    ASPECT_MAP = {
        '1:1': 'square', '16:9': 'landscape_16_9', '4:5': 'portrait_4_5',
        '9:16': 'portrait_9_16', '4:3': 'landscape_4_3',
    }

    results = []
    for item in prompt_items:
        prompt_text = item.get('prompt', '')
        aspect = ASPECT_MAP.get(item.get('aspect', '1:1'), 'square')
        role = item.get('role', '이미지')
        try:
            resp = req_lib.post(
                'https://fal.run/fal-ai/flux/schnell',
                headers={'Authorization': f'Key {api_key}', 'Content-Type': 'application/json'},
                json={'prompt': prompt_text, 'image_size': aspect, 'num_images': 1, 'num_inference_steps': 4},
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()
            img_url = (data.get('images') or [{}])[0].get('url', '')

            # Supabase Storage에 저장
            if img_url:
                r2 = req_lib.get(img_url, timeout=30)
                r2.raise_for_status()
                path = f'{current_user.id}/studio/{product_id[:8]}_{uuid.uuid4().hex[:8]}.jpg'
                supabase.storage.from_('creations').upload(path, r2.content, {'content-type': 'image/jpeg'})
                stored_url = supabase.storage.from_('creations').get_public_url(path)
                results.append({'role': role, 'url': stored_url, 'ok': True})
            else:
                results.append({'role': role, 'ok': False, 'message': '이미지 URL 없음'})
        except Exception as e:
            logger.error(f'[STUDIO] generate_images error ({role}): {e}')
            results.append({'role': role, 'ok': False, 'message': str(e)[:100]})

    success = [r for r in results if r.get('ok')]
    return jsonify(ok=bool(success), results=results,
                   message=f'{len(success)}/{len(results)}장 생성 완료')


# ── 헬퍼 ────────────────────────────────────────────────
def _product_to_input(product: dict) -> dict:
    features = product.get('features') or []
    return {
        'product_id': product['id'],
        'topic': product['name'],
        'seo_keywords': ', '.join(features),
        'purpose': '상품 홍보',
        'length': '2000',
        'product_name': product['name'],
        'product_price': product.get('price', ''),
        'product_category': product.get('category', ''),
        'product_features': features,
        'product_url': product.get('product_url', ''),
    }


def _build_product_prompt(creation_type: str, brand: dict, product: dict):
    from services.claude_service import build_brand_context, SYSTEM_BASE

    features_str = '\n'.join(f'- {f}' for f in (product.get('features') or []))
    price_str = f"{product['price']:,}원" if product.get('price') else '가격 미설정'
    product_ctx = f"""
[상품 정보]
- 상품명: {product['name']}
- 카테고리: {product.get('category', '')}
- 가격: {price_str}
- 핵심 특징:
{features_str}
- 상품 설명: {product.get('description', '')}
"""
    brand_ctx = build_brand_context(brand)
    system = f"{SYSTEM_BASE}\n\n[브랜드 정보]\n{brand_ctx}"

    if creation_type == 'blog':
        user = f"""{product_ctx}
위 상품을 홍보하는 SEO 최적화 블로그 포스트를 작성해 주세요.
- 제목 3가지 (클릭률 최적화)
- 본문 2,000자 내외
- 마무리 CTA 문구
- 해시태그 10개"""

    elif creation_type == 'instagram':
        user = f"""{product_ctx}
위 상품을 홍보하는 인스타그램 콘텐츠를 작성해 주세요.
- 캡션 3가지 버전 (짧/중/긴)
- 각 버전별 해시태그 30개"""

    elif creation_type == 'detail_page':
        user = f"""{product_ctx}
위 상품의 상세페이지 카피를 작성해 주세요.
- 상단 훅 문구 (3초 안에 구매욕 자극)
- 핵심 기능 설명 (기능별 카피)
- 고객 후기 포맷 3개
- CTA 문구 5종
- FAQ 5개"""

    elif creation_type == 'ad_copy':
        user = f"""{product_ctx}
위 상품의 광고 카피를 작성해 주세요.
- 헤드라인 5종 (감성/혜택/비교/호기심/긴급 소구)
- 각 헤드라인별 본문 1~2줄
- CTA 문구"""

    elif creation_type == 'thumbnail_text':
        user = f"""{product_ctx}
위 상품 썸네일에 들어갈 짧은 문구를 작성해 주세요.
- A/B/C/D/E 5종 (각 15자 이내)
- 각 문구의 소구 포인트 설명"""

    else:
        user = f"{product_ctx}\n{creation_type} 콘텐츠를 작성해 주세요."

    return system, user
