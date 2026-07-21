"""배너 이미지 생성 라우트"""
import logging
import uuid

from flask import render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from blueprints.create import create_bp
from blueprints.create._base import get_accessible_brands, get_default_brand, get_brand_by_id
from models import POINT_COSTS
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


def _get_product(supabase, product_id: str):
    """소유권 검증 포함 — product.py::_get_product 와 동일한 OR 매칭 정책."""
    if not product_id:
        return None
    r = supabase.table('products').select('*').eq('id', product_id).execute()
    p = r.data[0] if r.data else None
    if not p:
        return None
    if getattr(current_user, 'is_superadmin', False):
        return p
    if (getattr(current_user, 'operator_id', None)
            and p.get('operator_id') == current_user.operator_id):
        return p
    if p.get('user_id') == str(current_user.id):
        return p
    return None


def _first_product_image(product: dict | None) -> str | None:
    """상품의 첫 번째 이미지 URL 반환."""
    if not product:
        return None
    imgs = product.get('images') or []
    if isinstance(imgs, list) and imgs:
        return imgs[0] if isinstance(imgs[0], str) else None
    return None


# ─────────────────────────────────────────────────────────────
# 페이지
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner')
@login_required
def banner():
    supabase = current_app.supabase
    brands   = get_accessible_brands(supabase)
    default  = get_default_brand(supabase)

    products = []
    if default:
        r = supabase.table('products').select('id,name,category,images').eq(
            'brand_id', default['id']
        ).order('created_at', desc=True).limit(50).execute()
        products = r.data or []

    from services.banner_service import (BANNER_SIZES, BANNER_LAYOUTS, BANNER_BG_TYPES,
                                         TEXT_BANNER_BG_TYPES, TEXT_BANNER_LAYOUTS,
                                         PRODUCT_BANNER_BG_PRESETS)
    return render_template(
        'create/banner.html',
        brands=brands,
        default_brand=default,
        products=products,
        banner_sizes=BANNER_SIZES,
        banner_layouts=BANNER_LAYOUTS,
        banner_bg_types=BANNER_BG_TYPES,
        text_banner_bg_types=TEXT_BANNER_BG_TYPES,
        text_banner_layouts=TEXT_BANNER_LAYOUTS,
        product_banner_bg_presets=PRODUCT_BANNER_BG_PRESETS,
        cost=POINT_COSTS.get('banner', 80),
        product_cost=POINT_COSTS.get('banner_product', 80),
        text_cost=POINT_COSTS.get('banner_text', 20),
    )


# ─────────────────────────────────────────────────────────────
# 의도 분석 → 배너 설정 추천 (무료, Haiku)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner/analyze', methods=['POST'])
@login_required
def banner_analyze():
    """자유 텍스트 의도 → 사이즈·레이아웃·문구·배경 프롬프트 자동 추천 (무료)."""
    supabase   = current_app.supabase
    data       = request.get_json(force=True) or {}
    brand_id   = (data.get('brand_id')   or '').strip()
    product_id = (data.get('product_id') or '').strip()
    intent     = (data.get('intent')     or '').strip()

    if not intent:
        return jsonify(ok=False, message='배너 목적을 입력해주세요.')

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id)

    from services.banner_service import analyze_banner_intent
    try:
        result = analyze_banner_intent(brand, product, intent)
        return jsonify(ok=True, **result)
    except Exception as e:
        logger.error('[banner/analyze] %s', e)
        return jsonify(ok=False, message=f'분석 실패: {e}')


# ─────────────────────────────────────────────────────────────
# 브랜드 변경 시 상품 목록
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner/products', methods=['GET'])
@login_required
def banner_products():
    supabase = current_app.supabase
    brand_id = request.args.get('brand_id', '').strip()
    if not brand_id or not get_brand_by_id(supabase, brand_id):
        return jsonify(ok=True, products=[])
    r = supabase.table('products').select('id,name,category,images').eq(
        'brand_id', brand_id
    ).order('created_at', desc=True).limit(50).execute()
    return jsonify(ok=True, products=r.data or [])


# ─────────────────────────────────────────────────────────────
# 문구 생성 (무료)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner/copy', methods=['POST'])
@login_required
def banner_copy():
    """배너 헤드라인·서브라인·CTA 문구 생성 (무료)."""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    brand_id    = (data.get('brand_id')   or '').strip()
    product_id  = (data.get('product_id') or '').strip()
    purpose     = (data.get('purpose')    or '').strip()
    size_key    = (data.get('size_key')   or 'sns_square').strip()

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id)

    from services.claude_service import build_brand_context
    from services.banner_service import BANNER_SIZES, generate_banner_copy

    brand_ctx   = build_brand_context(brand, product)
    size_meta   = BANNER_SIZES.get(size_key, BANNER_SIZES['sns_square'])
    size_label  = size_meta['label']
    has_product = bool(_first_product_image(product))

    try:
        copy = generate_banner_copy(brand_ctx, purpose, size_label, has_product)
        return jsonify(ok=True, **copy)
    except Exception as e:
        logger.error('[banner/copy] %s', e)
        return jsonify(ok=False, message=f'문구 생성 실패: {e}')


# ─────────────────────────────────────────────────────────────
# 배너 이미지 생성 (80P, 비동기)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner/generate', methods=['POST'])
@login_required
def banner_generate():
    """배너 이미지 생성 시작 → creation_id 즉시 반환, 백그라운드 진행 (80P)."""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    brand_id     = (data.get('brand_id')     or '').strip()
    product_id   = (data.get('product_id')   or '').strip()
    headline     = (data.get('headline')     or '').strip()
    subline      = (data.get('subline')      or '').strip()
    cta          = (data.get('cta')          or '').strip()
    bg_type      = (data.get('bg_type')      or 'flux_ai').strip()
    bg_prompt    = (data.get('bg_prompt')    or '').strip()
    brand_color  = (data.get('brand_color')  or '#e8355a').strip()
    layout       = (data.get('layout')       or 'overlay').strip()
    size_key     = (data.get('size_key')     or 'sns_square').strip()
    custom_w     = data.get('custom_w')
    custom_h     = data.get('custom_h')
    use_product_img = bool(data.get('use_product_img', True))

    if not headline:
        return jsonify(ok=False, message='제목 문구가 필요합니다.')

    from services.banner_service import BANNER_SIZES

    size_meta = BANNER_SIZES.get(size_key, BANNER_SIZES['sns_square'])
    if size_key == 'custom' and custom_w and custom_h:
        W = max(200, min(2400, int(custom_w)))
        H = max(200, min(2400, int(custom_h)))
    elif size_meta['w']:
        W, H = size_meta['w'], size_meta['h']
    else:
        W, H = 1080, 1080

    cost = POINT_COSTS.get('banner', 80)
    from services.point_service import get_balance, use_points, InsufficientPoints
    balance = get_balance(current_user)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    # 제품 이미지 URL
    product_url: str | None = None
    if use_product_img:
        product = _get_product(supabase, product_id)
        product_url = _first_product_image(product)

    if brand_id and not get_brand_by_id(supabase, brand_id):
        brand_id = ''   # 소유 안 한 브랜드로 태깅 방지

    creation_id = str(uuid.uuid4())
    try:
        _row = {
            'id': creation_id,
            'user_id': current_user.id,
            'brand_id': brand_id or None,
            'creation_type': 'banner',
            'input_data': {
                'size_key': size_key, 'W': W, 'H': H,
                'layout': layout, 'bg_type': bg_type,
                'headline': headline, 'subline': subline, 'cta': cta,
            },
            'output_data': {'progress': 0, 'step': '준비 중'},
            'points_used': cost,
            'status': 'generating',
            'model_used': 'flux+pil',
            'created_at': now_kst().isoformat(),
        }
        if getattr(current_user, 'operator_id', None):
            _row['operator_id'] = current_user.operator_id
        supabase.table('creations').insert(_row).execute()
    except Exception as e:
        logger.warning('[banner/generate] creation insert: %s', e)

    try:
        use_points(current_user, 'banner', creation_id)
    except InsufficientPoints:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message='포인트가 부족합니다.')

    # Celery 워커에서 실행
    from tasks.banner_task import generate_banner
    supabase_url = current_app.config.get('SUPABASE_URL', '')
    supabase_key = (current_app.config.get('SUPABASE_SERVICE_KEY')
                    or current_app.config.get('SUPABASE_KEY', ''))

    generate_banner.delay(
        creation_id=creation_id,
        user_id=current_user.id,
        headline=headline,
        subline=subline,
        cta=cta,
        bg_type=bg_type,
        bg_prompt=bg_prompt,
        brand_color=brand_color,
        layout=layout,
        W=W,
        H=H,
        product_url=product_url,
        supabase_url=supabase_url,
        supabase_key=supabase_key,
    )
    return jsonify(ok=True, creation_id=creation_id, cost=cost)


# ─────────────────────────────────────────────────────────────
# 텍스트 배너 즉시 생성 (PIL만, 20P, 동기)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner/text-generate', methods=['POST'])
@login_required
def banner_text_generate():
    """텍스트 배너 즉시 생성 → 포인트 차감 후 image_url 반환 (20P, 동기)."""
    import base64 as _b64
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    headline    = (data.get('headline')         or '').strip()
    subline     = (data.get('subline')          or '').strip()
    cta         = (data.get('cta')              or '').strip()
    bg_type     = (data.get('bg_type')          or 'solid').strip()
    bg_color1   = (data.get('bg_color1')        or '#3b82f6').strip()
    bg_color2   = (data.get('bg_color2')        or '#1d4ed8').strip()
    layout      = (data.get('layout')           or 'center').strip()
    text_color  = (data.get('text_color')       or '#ffffff').strip()
    cta_color   = (data.get('cta_color')        or bg_color1).strip()
    size_key    = (data.get('size_key')         or 'sns_square').strip()
    custom_w    = data.get('custom_w')
    custom_h    = data.get('custom_h')
    brand_id    = (data.get('brand_id')         or '').strip()
    product_id  = (data.get('product_id')       or '').strip()
    use_product = bool(data.get('use_product_img', True))
    if brand_id and not get_brand_by_id(supabase, brand_id):
        brand_id = ''   # 소유 안 한 브랜드로 태깅 방지

    if not headline:
        return jsonify(ok=False, message='제목 문구가 필요합니다.')

    from services.banner_service import BANNER_SIZES, generate_text_banner

    size_meta = BANNER_SIZES.get(size_key, BANNER_SIZES['sns_square'])
    if size_key == 'custom' and custom_w and custom_h:
        W = max(200, min(2400, int(custom_w)))
        H = max(200, min(2400, int(custom_h)))
    elif size_meta['w']:
        W, H = size_meta['w'], size_meta['h']
    else:
        W, H = 1080, 1080

    cost = POINT_COSTS.get('banner_text', 20)
    from services.point_service import get_balance, use_points, InsufficientPoints
    if get_balance(current_user) < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P)')

    product_url: str | None = None
    if use_product and product_id:
        product = _get_product(supabase, product_id)
        product_url = _first_product_image(product)

    creation_id = str(uuid.uuid4())

    # 1) 포인트 선차감 (생성 전 차감 — race condition 방지)
    try:
        use_points(current_user, 'banner_text', creation_id)
    except InsufficientPoints:
        return jsonify(ok=False, message='포인트가 부족합니다.')

    # 2) PIL 생성
    try:
        b64_uri = generate_text_banner(
            bg_type=bg_type, bg_color1=bg_color1, bg_color2=bg_color2,
            layout=layout, headline=headline, subline=subline, cta=cta,
            text_color=text_color, cta_color=cta_color,
            W=W, H=H, product_url=product_url,
        )
    except Exception as e:
        logger.error('[banner/text-generate] PIL 실패: %s', e)
        return jsonify(ok=False, message=f'배너 생성 실패: {e}')

    # 3) Supabase Storage 업로드
    try:
        _, b64data = b64_uri.split(',', 1)
        img_bytes  = _b64.b64decode(b64data)
        path       = f'{current_user.id}/{creation_id}_text_banner.jpg'
        supabase.storage.from_('creations').upload(
            path, img_bytes, {'content-type': 'image/jpeg'}
        )
        image_url = supabase.storage.from_('creations').get_public_url(path)
    except Exception as e:
        logger.error('[banner/text-generate] 업로드 실패: %s', e)
        return jsonify(ok=False, message=f'업로드 실패: {e}')

    # 4) creation 행 기록
    try:
        _row = {
            'id': creation_id, 'user_id': current_user.id,
            'brand_id': brand_id or None,
            'creation_type': 'banner_text',
            'input_data': {'size_key': size_key, 'W': W, 'H': H,
                           'bg_type': bg_type, 'layout': layout,
                           'headline': headline, 'subline': subline, 'cta': cta},
            'output_data': {'image_url': image_url, 'W': W, 'H': H},
            'points_used': cost, 'status': 'done',
            'model_used': 'pil_only',
            'created_at': now_kst().isoformat(),
        }
        if getattr(current_user, 'operator_id', None):
            _row['operator_id'] = current_user.operator_id
        supabase.table('creations').insert(_row).execute()
    except Exception as e:
        logger.warning('[banner/text-generate] creation insert 실패: %s', e)

    return jsonify(ok=True, image_url=image_url, creation_id=creation_id, W=W, H=H, size_key=size_key)


# ─────────────────────────────────────────────────────────────
# 상품 배너 생성 (Bria 배경교체 + PIL, 80P, 비동기)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner/product-generate', methods=['POST'])
@login_required
def banner_product_generate():
    """상품 이미지 배경 교체 배너 생성 (80P, Celery 비동기)."""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    brand_id        = (data.get('brand_id')         or '').strip()
    if brand_id and not get_brand_by_id(supabase, brand_id):
        brand_id = ''   # 소유 안 한 브랜드로 태깅 방지
    product_id      = (data.get('product_id')       or '').strip()
    headline        = (data.get('headline')         or '').strip()
    subline         = (data.get('subline')          or '').strip()
    cta             = (data.get('cta')              or '').strip()
    bg_preset       = (data.get('bg_preset')        or 'studio_white').strip()
    bg_prompt_custom= (data.get('bg_prompt_custom') or '').strip()
    brand_color     = (data.get('brand_color')      or '#e8355a').strip()
    layout          = (data.get('layout')           or 'overlay').strip()
    size_key        = (data.get('size_key')         or 'sns_square').strip()
    custom_w        = data.get('custom_w')
    custom_h        = data.get('custom_h')

    if not headline:
        return jsonify(ok=False, message='제목 문구가 필요합니다.')

    from services.banner_service import BANNER_SIZES

    size_meta = BANNER_SIZES.get(size_key, BANNER_SIZES['sns_square'])
    if size_key == 'custom' and custom_w and custom_h:
        W = max(200, min(2400, int(custom_w)))
        H = max(200, min(2400, int(custom_h)))
    elif size_meta['w']:
        W, H = size_meta['w'], size_meta['h']
    else:
        W, H = 1080, 1080

    # 상품 이미지 필수
    product = _get_product(supabase, product_id)
    product_url = _first_product_image(product)
    if not product_url:
        return jsonify(ok=False, message='상품 이미지가 없습니다. 상품 관리에서 이미지를 먼저 등록해주세요.')

    cost = POINT_COSTS.get('banner_product', 80)
    from services.point_service import get_balance, use_points, InsufficientPoints
    balance = get_balance(current_user)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    creation_id = str(uuid.uuid4())
    try:
        _row = {
            'id': creation_id,
            'user_id': current_user.id,
            'brand_id': brand_id or None,
            'creation_type': 'banner_product',
            'input_data': {
                'size_key': size_key, 'W': W, 'H': H,
                'layout': layout, 'bg_preset': bg_preset,
                'headline': headline, 'subline': subline, 'cta': cta,
            },
            'output_data': {'progress': 0, 'step': '준비 중'},
            'points_used': cost,
            'status': 'generating',
            'model_used': 'bria+pil',
            'created_at': now_kst().isoformat(),
        }
        if getattr(current_user, 'operator_id', None):
            _row['operator_id'] = current_user.operator_id
        supabase.table('creations').insert(_row).execute()
    except Exception as e:
        logger.warning('[banner/product-generate] creation insert: %s', e)

    try:
        use_points(current_user, 'banner_product', creation_id)
    except InsufficientPoints:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message='포인트가 부족합니다.')

    from tasks.banner_task import generate_product_banner
    supabase_url = current_app.config.get('SUPABASE_URL', '')
    supabase_key = (current_app.config.get('SUPABASE_SERVICE_KEY')
                    or current_app.config.get('SUPABASE_KEY', ''))

    generate_product_banner.delay(
        creation_id=creation_id,
        user_id=current_user.id,
        headline=headline,
        subline=subline,
        cta=cta,
        bg_preset=bg_preset,
        bg_prompt_custom=bg_prompt_custom,
        brand_color=brand_color,
        layout=layout,
        W=W, H=H,
        product_url=product_url,
        supabase_url=supabase_url,
        supabase_key=supabase_key,
    )
    return jsonify(ok=True, creation_id=creation_id, cost=cost)


# ─────────────────────────────────────────────────────────────
# 상태 폴링
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner/status/<creation_id>', methods=['GET'])
@login_required
def banner_status(creation_id: str):
    supabase = current_app.supabase
    r = supabase.table('creations').select('status,output_data').eq(
        'id', creation_id
    ).eq('user_id', current_user.id).execute()
    if not r.data:
        return jsonify(ok=False, message='없는 작업입니다.')
    row = r.data[0]
    return jsonify(ok=True, status=row['status'], output_data=row.get('output_data') or {})
