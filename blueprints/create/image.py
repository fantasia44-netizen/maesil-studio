"""이미지 생성 라우트"""
import logging
from flask import request, jsonify, current_app
from flask_login import login_required, current_user
from blueprints.create import create_bp
from models import POINT_COSTS
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)

ENGINE_COST_MAP = {
    'flux_preview':  'img_preview',
    'flux_standard': 'img_standard',
    'flux_hq':       'img_hq',
    'ideogram':      'img_ideogram',
    'card_news':     'img_card_news',
}


@create_bp.route('/image/generate', methods=['POST'])
@login_required
def image_generate():
    supabase = current_app.supabase
    data = request.json or {}

    engine      = data.get('engine', 'flux_standard')
    prompt      = data.get('prompt', '').strip()
    size        = data.get('size', '1024x1024')
    style_preset = data.get('style_preset')
    brand_color  = data.get('brand_color', '#e8355a')
    texts        = data.get('texts', [])   # 카드뉴스 전용
    # img2img 전용 — 제품 레퍼런스 이미지
    reference_image_url = (data.get('reference_image_url') or '').strip() or None
    strength            = float(data.get('strength', 0.80))

    if not prompt:
        return jsonify(ok=False, message='프롬프트를 입력하세요.')

    # img2img 는 FLUX Dev 기준으로 비용 산정 (flux_standard 동일)
    cost_key = ENGINE_COST_MAP.get(engine, 'img_standard')
    cost = POINT_COSTS.get(cost_key, 300)

    # 포인트 확인
    from services.point_service import get_balance, use_points, InsufficientPoints
    balance = get_balance(current_user.id)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    # creation 행 생성
    import uuid
    creation_id = str(uuid.uuid4())
    try:
        supabase.table('creations').insert({
            'id': creation_id,
            'user_id': current_user.id,
            'creation_type': cost_key,
            'input_data': {'prompt': prompt, 'engine': engine, 'size': size},
            'output_data': {},
            'points_used': cost,
            'status': 'generating',
            'model_used': engine,
            'created_at': now_kst().isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f'[IMAGE] creation insert error: {e}')

    import time
    start = time.time()
    try:
        from services.imagen_service import generate_image, generate_card_news, upload_to_supabase

        if engine == 'card_news':
            image_url = generate_card_news(texts or [prompt], prompt, brand_color)
        else:
            image_url = generate_image(
                prompt, engine, style_preset, size, brand_color,
                reference_image_url=reference_image_url,
                strength=strength,
            )

        # Supabase Storage 업로드
        filename = f'{engine}_{creation_id[:8]}.jpg'
        public_url = upload_to_supabase(image_url, current_user.id, filename)

        gen_ms = int((time.time() - start) * 1000)

        # 포인트 차감
        try:
            use_points(current_user.id, cost_key, creation_id)
        except InsufficientPoints:
            supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
            return jsonify(ok=False, message='포인트가 부족합니다.')

        supabase.table('creations').update({
            'output_data': {'image_url': public_url},
            'status': 'done',
            'generation_ms': gen_ms,
        }).eq('id', creation_id).execute()

        return jsonify(ok=True, image_url=public_url, creation_id=creation_id, cost=cost)

    except Exception as e:
        logger.error(f'[IMAGE] generate error: {e}')
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message=f'이미지 생성 실패: {str(e)}')


# ──────────────────────────────────────────
# 배경 제거
# ──────────────────────────────────────────
@create_bp.route('/image/remove-bg', methods=['POST'])
@login_required
def remove_bg():
    """배경 제거
    - mode=basic  : rembg (무료)
    - mode=advanced: fal.ai BiRefNet (20P)
    multipart/form-data: file=<image>, mode=<basic|advanced>
    """
    mode = request.form.get('mode', 'basic')
    file = request.files.get('file')
    if not file:
        return jsonify(ok=False, message='이미지를 업로드하세요.')

    image_bytes = file.read()

    try:
        from services.bg_service import (
            remove_bg_basic, remove_bg_advanced, image_bytes_to_data_url
        )

        if mode == 'advanced':
            # 포인트 차감
            from services.point_service import get_balance, use_points, InsufficientPoints
            cost = POINT_COSTS.get('bg_remove_adv', 20)
            balance = get_balance(current_user.id)
            if balance < cost:
                return jsonify(ok=False, message=f'포인트 부족 (필요: {cost}P, 잔액: {balance}P)')
            result_bytes = remove_bg_advanced(image_bytes)
            import uuid
            use_points(current_user.id, 'bg_remove_adv', str(uuid.uuid4()))
        else:
            result_bytes = remove_bg_basic(image_bytes)

        data_url = image_bytes_to_data_url(result_bytes, 'image/png')
        return jsonify(ok=True, image=data_url, mode=mode)

    except Exception as e:
        logger.error(f'[BG] remove_bg error: {e}')
        return jsonify(ok=False, message=str(e))
