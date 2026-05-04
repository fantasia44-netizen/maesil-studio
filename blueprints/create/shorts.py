"""쇼츠/릴스 영상 생성 라우트"""
import json
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
    if not product_id:
        return None
    r = supabase.table('products').select('*').eq('id', product_id).execute()
    return r.data[0] if r.data else None


# ─────────────────────────────────────────────────────────────
# 페이지
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts')
@login_required
def shorts():
    supabase = current_app.supabase
    brands   = get_accessible_brands(supabase)
    default  = get_default_brand(supabase)

    products = []
    if default:
        r = supabase.table('products').select('id,name,category,images').eq(
            'brand_id', default['id']
        ).order('created_at', desc=True).limit(50).execute()
        products = r.data or []

    return render_template('create/shorts.html',
                           brands=brands,
                           default_brand=default,
                           products=products)


# ─────────────────────────────────────────────────────────────
# 소구포인트 생성 (인스타와 동일 로직 재사용)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/angles', methods=['POST'])
@login_required
def shorts_angles():
    """소구포인트 3개 생성"""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}
    brand_id    = (data.get('brand_id')   or '').strip()
    product_id  = (data.get('product_id') or '').strip()
    direction   = (data.get('direction')  or '').strip()

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id)

    from services.claude_service import build_brand_context, generate_text
    import re as _re

    brand_ctx = build_brand_context(brand, product)
    system = '당신은 숏폼 영상 전문 마케터입니다. 순수 JSON만 출력하세요.'
    prompt = f"""인스타 릴스/유튜브 쇼츠용 소구포인트 3개를 JSON으로 생성하세요.

[브랜드·상품]
{brand_ctx}

[게시 방향]
{direction or '브랜드 전반적 인지도 향상'}

[출력 형식 — 순수 JSON 배열]
[
  {{
    "title":       "소구포인트 한 줄 (15자 이내)",
    "hook":        "훅 문구 — 시청자가 멈출 만한 첫 문장 (20자 이내)",
    "image_vibe":  "영상 분위기 키워드 (예: 감성적·역동적·귀여운)",
    "target_pain": "타겟의 핵심 불편/욕구 (20자 이내)"
  }},
  ...3개...
]

순수 JSON 배열만 출력."""

    try:
        raw   = generate_text(system, prompt, max_tokens=600, model='claude-haiku-4-5-20251001')
        clean = _re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=_re.MULTILINE).strip()
        s, e  = clean.find('['), clean.rfind(']') + 1
        if s >= 0 and e > s:
            clean = clean[s:e]
        angles = json.loads(clean)
        return jsonify(ok=True, angles=angles[:3])
    except Exception as ex:
        logger.error('[shorts/angles] %s', ex)
        return jsonify(ok=False, message=f'소구포인트 생성 실패: {ex}')


# ─────────────────────────────────────────────────────────────
# 대본 생성
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/script', methods=['POST'])
@login_required
def shorts_script():
    """5씬 쇼츠 대본 생성 (무료 — 포인트는 영상 생성 시 통합 차감)"""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    brand_id   = (data.get('brand_id')   or '').strip()
    product_id = (data.get('product_id') or '').strip()
    angle      = data.get('angle') or {}
    style      = (data.get('style') or 'realistic_banner').strip()

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id)

    from services.claude_service import build_brand_context
    from services.shorts_service import generate_shorts_script

    brand_ctx = build_brand_context(brand, product)
    creation_id = str(uuid.uuid4())

    try:
        supabase.table('creations').insert({
            'id': creation_id,
            'user_id': current_user.id,
            'brand_id': brand['id'],
            'creation_type': 'shorts_script',
            'input_data': {'angle': angle, 'style': style},
            'output_data': {},
            'points_used': 0,
            'status': 'generating',
            'model_used': 'claude-haiku-4-5-20251001',
            'created_at': now_kst().isoformat(),
        }).execute()
    except Exception as e:
        logger.warning('[shorts/script] creation insert: %s', e)

    try:
        scenes = generate_shorts_script(brand_ctx, angle, style)
        # 포인트 차감 없음 — 영상 생성(shorts/generate)에서 300P 통합 차감

        supabase.table('creations').update({
            'output_data': {'scenes': scenes},
            'status': 'done',
        }).eq('id', creation_id).execute()

        return jsonify(ok=True, scenes=scenes, creation_id=creation_id)

    except InsufficientPoints:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message='포인트가 부족합니다.')
    except Exception as e:
        logger.error('[shorts/script] %s', e)
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message=f'대본 생성 실패: {e}')


# ─────────────────────────────────────────────────────────────
# 영상 생성 (비동기 백그라운드)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/generate', methods=['POST'])
@login_required
def shorts_generate():
    """영상 생성 시작 → creation_id 즉시 반환, 백그라운드에서 진행 (300P)"""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    scenes      = data.get('scenes') or []
    style       = (data.get('style')       or 'realistic_banner').strip()
    brand_color = (data.get('brand_color') or '#e8355a').strip()
    voice_key   = (data.get('voice')       or 'female_natural').strip()
    tts_speed   = float(data.get('tts_speed') or 1.1)
    brand_id    = (data.get('brand_id')    or '').strip()

    if not scenes:
        return jsonify(ok=False, message='씬 데이터가 없습니다. 먼저 대본을 생성하세요.')

    cost = POINT_COSTS.get('shorts_video', 300)
    from services.point_service import get_balance, use_points, InsufficientPoints
    balance = get_balance(current_user.id)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    creation_id = str(uuid.uuid4())
    try:
        supabase.table('creations').insert({
            'id': creation_id,
            'user_id': current_user.id,
            'brand_id': brand_id or None,
            'creation_type': 'shorts_video',
            'input_data': {'style': style, 'voice': voice_key, 'scenes': scenes},
            'output_data': {'progress': 0, 'step': '준비 중'},
            'points_used': cost,
            'status': 'generating',
            'model_used': f'flux+tts+ffmpeg',
            'created_at': now_kst().isoformat(),
        }).execute()
    except Exception as e:
        logger.warning('[shorts/generate] creation insert: %s', e)

    try:
        use_points(current_user.id, 'shorts_video', creation_id)
    except InsufficientPoints:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message='포인트가 부족합니다.')

    from services.shorts_service import start_shorts_pipeline
    from flask import current_app
    start_shorts_pipeline(
        creation_id=creation_id,
        user_id=current_user.id,
        scenes=scenes,
        style=style,
        brand_color=brand_color,
        voice_key=voice_key,
        tts_speed=tts_speed,
        supabase=supabase,
        app=current_app._get_current_object(),
    )

    return jsonify(ok=True, creation_id=creation_id, cost=cost)


# ─────────────────────────────────────────────────────────────
# 상태 폴링
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/status/<creation_id>', methods=['GET'])
@login_required
def shorts_status(creation_id: str):
    supabase = current_app.supabase
    r = supabase.table('creations').select('status,output_data').eq(
        'id', creation_id
    ).eq('user_id', current_user.id).execute()

    if not r.data:
        return jsonify(ok=False, message='없는 작업입니다.')

    row = r.data[0]
    return jsonify(
        ok=True,
        status=row['status'],
        output_data=row.get('output_data') or {},
    )


# ─────────────────────────────────────────────────────────────
# 브랜드 변경 시 상품 목록 갱신
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/products', methods=['GET'])
@login_required
def shorts_products():
    supabase = current_app.supabase
    brand_id = request.args.get('brand_id', '').strip()
    if not brand_id:
        return jsonify(ok=True, products=[])
    r = supabase.table('products').select('id,name,category,images').eq(
        'brand_id', brand_id
    ).order('created_at', desc=True).limit(50).execute()
    return jsonify(ok=True, products=r.data or [])
