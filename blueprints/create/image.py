"""이미지 생성 라우트"""
import logging
from flask import request, jsonify, current_app
from flask_login import login_required, current_user
from blueprints.create import create_bp
from models import POINT_COSTS
from services.rate_limiter import check_ai_rate_limit

logger = logging.getLogger(__name__)

ENGINE_COST_MAP = {
    'flux_preview':  'img_preview',   # FLUX Schnell 50P
    'flux_dev':      'img_dev',       # FLUX dev 80P — 인체/손 정확도↑
    'ideogram':      'img_ideogram',  # Ideogram 200P
    'card_news':     'img_card_news', # 카드뉴스 800P
    'bg_replace':    'bg_replace',    # 배경 교체 80P
}


@create_bp.route('/image/generate', methods=['POST'])
@login_required
def image_generate():
    """이미지 생성 — Celery 워커에 제출 후 즉시 반환. 완료 여부는 /image/status/<id> 폴링."""
    err = check_ai_rate_limit('image_generate', max_per_hour=100)
    if err:
        return jsonify(ok=False, message=err), 429
    data = request.json or {}

    engine      = data.get('engine', 'flux_standard')
    prompt      = data.get('prompt', '').strip()
    size        = data.get('size', '1024x1024')
    style_preset = data.get('style_preset')
    brand_color  = data.get('brand_color', '#e8355a')
    texts        = data.get('texts', [])   # 카드뉴스 전용
    # 배경 교체 전용 — 누끼컷 원본 URL
    reference_image_url = (data.get('reference_image_url') or '').strip() or None

    if not prompt:
        return jsonify(ok=False, message='프롬프트를 입력하세요.')
    if engine == 'bg_replace' and not reference_image_url:
        return jsonify(ok=False, message='배경 교체는 원본 이미지 URL이 필요합니다.')

    cost_key = ENGINE_COST_MAP.get(engine, 'img_preview')
    cost = POINT_COSTS.get(cost_key, 50)

    from services.async_generation import submit_async_generation, AsyncSubmitError
    from tasks.image_task import generate_image_task
    try:
        creation_id = submit_async_generation(
            owner=current_user, creation_type=cost_key, cost=cost,
            input_data={'prompt': prompt, 'engine': engine, 'size': size},
            model_used=engine,
            task_delay_fn=generate_image_task.delay,
            task_kwargs=dict(
                engine=engine, prompt=prompt, size=size, style_preset=style_preset,
                brand_color=brand_color, texts=texts,
                reference_image_url=reference_image_url,
            ),
        )
    except AsyncSubmitError as e:
        return jsonify(ok=False, message=str(e))
    except Exception as e:
        logger.error(f'[IMAGE] 제출 실패: {e}', exc_info=True)
        return jsonify(ok=False, message='이미지 생성 요청 중 오류가 발생했습니다.')

    return jsonify(ok=True, id=creation_id, async_mode=True, cost=cost)


@create_bp.route('/image/status/<creation_id>', methods=['GET'])
@login_required
def image_status(creation_id):
    """이미지 생성 Celery 태스크 완료 여부 폴링."""
    from services.async_generation import render_status_response
    supabase = current_app.supabase
    if not supabase:
        return jsonify(ok=False, status='error', message='DB 연결이 없습니다.')
    try:
        r = supabase.table('creations').select(
            'id, status, output_data, user_id'
        ).eq('id', creation_id).single().execute()
        row = r.data
    except Exception:
        return jsonify(ok=False, status='error', message='조회 실패')

    return render_status_response(
        row, current_user.id,
        done_fields={'image_url': 'image_url', 'translated_prompt': 'translated_prompt'},
    )


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
            from services.point_service import get_balance, use_points, InsufficientPoints
            cost = POINT_COSTS.get('bg_remove_adv', 20)
            balance = get_balance(current_user)
            if balance < cost:
                return jsonify(ok=False, message=f'포인트 부족 (필요: {cost}P, 잔액: {balance}P)')
            import uuid
            ref_id = str(uuid.uuid4())
            # 포인트 선차감 후 처리
            try:
                use_points(current_user, 'bg_remove_adv', ref_id)
            except InsufficientPoints:
                return jsonify(ok=False, message=f'포인트 부족 (필요: {cost}P, 잔액: {balance}P)')
            result_bytes = remove_bg_advanced(image_bytes)
        else:
            result_bytes = remove_bg_basic(image_bytes)

        data_url = image_bytes_to_data_url(result_bytes, 'image/png')
        return jsonify(ok=True, image=data_url, mode=mode)

    except Exception as e:
        logger.error(f'[BG] remove_bg error: {e}')
        return jsonify(ok=False, message=str(e))


# ──────────────────────────────────────────
# 에셋 업로드 (패널 수동 이미지)
# ──────────────────────────────────────────
@create_bp.route('/upload-asset', methods=['POST'])
@login_required
def upload_asset():
    """스토리 패널 등에서 실제 이미지를 직접 업로드할 때 사용.
    multipart/form-data: file=<image>
    반환: { ok: true, url: "https://..." }
    """
    file = request.files.get('file')
    if not file:
        return jsonify(ok=False, message='파일을 선택해주세요.')

    allowed = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
    mime = file.mimetype or 'image/jpeg'
    if mime not in allowed:
        return jsonify(ok=False, message='JPG, PNG, WEBP, GIF만 업로드 가능합니다.')

    image_bytes = file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        return jsonify(ok=False, message='파일 크기는 10MB 이하여야 합니다.')

    try:
        import uuid
        _EXT_MAP = {
            'image/jpeg': '.jpg', 'image/png': '.png',
            'image/webp': '.webp', 'image/gif': '.gif',
        }
        ext = _EXT_MAP.get(mime, '.jpg')
        filename = f'assets/{current_user.id}/{uuid.uuid4().hex}{ext}'
        supabase = current_app.supabase
        supabase.storage.from_('creations').upload(filename, image_bytes, {'content-type': mime})
        public_url = supabase.storage.from_('creations').get_public_url(filename)
        return jsonify(ok=True, url=public_url)
    except Exception as e:
        logger.error(f'[UPLOAD] upload_asset error: {e}')
        return jsonify(ok=False, message=f'업로드 실패: {e}')
