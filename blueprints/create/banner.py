"""배너 이미지 생성 라우트"""
import logging
import threading
import uuid

from flask import render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from blueprints.create import create_bp
from blueprints.create._base import get_accessible_brands, get_default_brand, get_brand_by_id
from models import POINT_COSTS
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


def _get_product(supabase, product_id: str):
    if not product_id:
        return None
    r = supabase.table('products').select('*').eq('id', product_id).execute()
    return r.data[0] if r.data else None


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

    from services.banner_service import BANNER_SIZES, BANNER_LAYOUTS, BANNER_BG_TYPES
    return render_template(
        'create/banner.html',
        brands=brands,
        default_brand=default,
        products=products,
        banner_sizes=BANNER_SIZES,
        banner_layouts=BANNER_LAYOUTS,
        banner_bg_types=BANNER_BG_TYPES,
        cost=POINT_COSTS.get('banner', 80),
    )


# ─────────────────────────────────────────────────────────────
# 브랜드 변경 시 상품 목록
# ─────────────────────────────────────────────────────────────

@create_bp.route('/banner/products', methods=['GET'])
@login_required
def banner_products():
    supabase = current_app.supabase
    brand_id = request.args.get('brand_id', '').strip()
    if not brand_id:
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
    balance = get_balance(current_user.id)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    # 제품 이미지 URL
    product_url: str | None = None
    if use_product_img:
        product = _get_product(supabase, product_id)
        product_url = _first_product_image(product)

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
        use_points(current_user.id, 'banner', creation_id)
    except InsufficientPoints:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message='포인트가 부족합니다.')

    # 백그라운드 실행
    from services.banner_service import run_banner_pipeline
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            run_banner_pipeline(
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
                supabase=supabase,
            )

    threading.Thread(target=_run, daemon=True).start()
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
