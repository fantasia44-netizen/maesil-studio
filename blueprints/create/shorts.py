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

    # 방향이 있을 때와 없을 때 프롬프트를 다르게 구성
    if direction:
        direction_block = f"""[사용자가 직접 지정한 소구 방향 — 반드시 이 내용을 중심으로 소구포인트를 작성하세요]
"{direction}"

※ 위 방향에서 벗어나거나 반대 의미의 소구포인트를 만들지 마세요.
※ 위 방향에 언급된 문제/상황을 problem 필드에 그대로 반영하세요.
※ 3개는 위 방향의 서로 다른 타겟층·표현 방식·각도로 변형하세요 (핵심 방향은 동일하게 유지)."""
    else:
        direction_block = '[소구 방향]\n상품의 핵심 문제 해결력 강조 — 타겟이 가장 공감할 불편함을 찾아 3가지 각도로 접근하세요.'

    system = (
        '당신은 숏폼 영상 전문 마케터입니다. '
        '사용자가 지정한 소구 방향을 최우선으로 따르며, '
        '타겟의 구체적인 문제를 정확히 짚고 상품과 연결하는 서사를 만듭니다. '
        '\n\n[한국 마케팅 지표 단위 — 반드시 준수]\n'
        '• ROAS: 한국에서는 % 단위 사용 (예: ROAS 500% = 광고비 대비 5배 매출).\n'
        '  사용자가 "ROAS 3", "ROAS 5" 등 배수로 입력해도 → "ROAS 300%", "ROAS 500%"로 변환하거나 "3배", "5배"로 표현.\n'
        '  "ROAS가 안 좋다" = 100~200% 이하 수준. "ROAS가 좋다" = 400~600% 이상 수준.\n'
        '• CPC: 원(₩) 단위 — "클릭당 800원", "CPC 1,200원"\n'
        '• CTR: % 단위 — "클릭률 2.5%"\n'
        '• 전환율/구매율: % 단위 — "전환율 3%"\n'
        '• 광고비·매출: 원(₩) 단위, 억/만 단위 사용 — "월 광고비 500만 원", "매출 2억"\n'
        '순수 JSON만 출력하세요.'
    )
    prompt = f"""아래 브랜드·상품의 쇼츠/릴스용 소구포인트 3개를 생성하세요.

[브랜드·상품]
{brand_ctx}

{direction_block}

[출력 형식 — 순수 JSON 배열]
[
  {{
    "title":    "소구포인트 제목 (15자 이내)",
    "problem":  "타겟이 실제로 겪는 구체적 불편·상황 (공감 가는 표현, 35자 이내. 예: '바쁜 아침마다 식사 챙기기가 너무 귀찮은 직장인')",
    "hook":     "그 문제를 겪는 사람이 스크롤 멈출 첫 마디 (20자 이내. 예: '아침마다 이거 하나로 해결됩니다')",
    "solution": "이 상품이 그 문제를 해결하는 방식 (30자 이내. 예: '5분 안에 완성되는 균형 잡힌 한 끼')",
    "result":   "해결 후 타겟이 얻는 변화·감정 (20자 이내. 예: '여유 있는 아침이 시작됩니다')",
    "image_vibe": "영상 분위기 키워드 (예: 따뜻한 일상·역동적·감성적·깔끔한 미니멀)"
  }},
  ...3개...
]

핵심: problem이 구체적이고 공감될수록, solution과의 연결이 명확할수록 좋습니다.
순수 JSON 배열만 출력."""

    try:
        raw   = generate_text(system, prompt, max_tokens=1000, model='claude-sonnet-4-6')
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

    brand_id     = (data.get('brand_id')   or '').strip()
    product_id   = (data.get('product_id') or '').strip()
    angle        = data.get('angle') or {}
    style        = (data.get('style') or 'realistic_banner').strip()
    reveal_mode  = bool(data.get('reveal_mode', False))

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id)

    from services.claude_service import build_brand_context
    from services.shorts_service import generate_shorts_script

    brand_ctx = build_brand_context(brand, product)
    creation_id = str(uuid.uuid4())

    # 제품 이미지 URL 추출 (리빌 모드에서 파이프라인에 전달용)
    product_image_url = None
    if reveal_mode and product:
        imgs = product.get('images') or []
        if isinstance(imgs, list) and imgs:
            product_image_url = imgs[0] if isinstance(imgs[0], str) else None

    try:
        _row = {
            'id': creation_id,
            'user_id': current_user.id,
            'brand_id': brand['id'],
            'creation_type': 'shorts_script',
            'input_data': {'angle': angle, 'style': style, 'reveal_mode': reveal_mode},
            'output_data': {},
            'points_used': 0,
            'status': 'generating',
            'model_used': 'claude-sonnet-4-6',
            'created_at': now_kst().isoformat(),
        }
        if getattr(current_user, 'operator_id', None):
            _row['operator_id'] = current_user.operator_id
        supabase.table('creations').insert(_row).execute()
    except Exception as e:
        logger.warning('[shorts/script] creation insert: %s', e)

    try:
        scenes = generate_shorts_script(brand_ctx, angle, style, reveal_mode=reveal_mode)

        supabase.table('creations').update({
            'output_data': {'scenes': scenes, 'product_image_url': product_image_url},
            'status': 'done',
        }).eq('id', creation_id).execute()

        return jsonify(
            ok=True,
            scenes=scenes,
            creation_id=creation_id,
            reveal_mode=reveal_mode,
            product_image_url=product_image_url,
        )

    except Exception as e:
        logger.error('[shorts/script] %s', e)
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message=f'대본 생성 실패: {e}')


# ─────────────────────────────────────────────────────────────
# 스토리보드 미리보기 (한글 3씬 방향 요약 — 이미지 생성 없음)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/storyboard', methods=['POST'])
@login_required
def shorts_storyboard():
    """3씬 스토리보드 방향 생성 (이미지/비디오 생성 없음, 빠름).

    사용자가 한글로 방향을 확인하고 승인한 뒤 이미지 생성 단계로 넘어감.
    Returns: scenes = [{role_ko, narration, overlay_title, scene_desc, flux_prompt}]
    """
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    brand_id    = (data.get('brand_id')    or '').strip()
    product_id  = (data.get('product_id')  or '').strip()
    angle       = data.get('angle') or {}
    style       = (data.get('style')       or 'realistic_banner').strip()
    reveal_mode = bool(data.get('reveal_mode', False))

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id)

    product_image_url = None
    if reveal_mode and product:
        imgs = product.get('images') or []
        if isinstance(imgs, list) and imgs:
            product_image_url = imgs[0] if isinstance(imgs[0], str) else None

    from services.claude_service import build_brand_context
    from services.shorts_service import generate_shorts_script

    brand_ctx = build_brand_context(brand, product)
    try:
        scenes = generate_shorts_script(brand_ctx, angle, style, reveal_mode=reveal_mode)
        return jsonify(
            ok=True,
            scenes=scenes,
            reveal_mode=reveal_mode,
            product_image_url=product_image_url,
        )
    except Exception as e:
        logger.error('[shorts/storyboard] %s', e)
        return jsonify(ok=False, message=f'스토리보드 생성 실패: {e}')


# ─────────────────────────────────────────────────────────────
# FLUX 기준 이미지 미리보기 (Kling 영상 시작 전 이미지 확인용)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/preview-image', methods=['POST'])
@login_required
def shorts_preview_image():
    """씬1 flux_prompt → FLUX 이미지 생성 → URL 반환.

    사용자가 이미지를 확인하고 [이 이미지로 진행] / [재생성] / [내 이미지 사용]을 선택.
    승인된 image_url을 /shorts/generate 에 ref_image_url로 전달하면 재생성 불필요.
    """
    data        = request.get_json(force=True) or {}
    flux_prompt = (data.get('flux_prompt')  or '').strip()
    scene_desc  = (data.get('scene_desc')   or '').strip()
    narration   = (data.get('narration')    or '').strip()
    style       = (data.get('style')        or 'realistic_banner').strip()

    # flux_prompt 없으면 scene_desc → narration 순으로 fallback
    if not flux_prompt:
        flux_prompt = scene_desc or narration or ''

    if not flux_prompt:
        return jsonify(ok=False, message='씬 프롬프트 정보가 없습니다. 스토리보드를 다시 생성해주세요.')

    from services.kling_service import ensure_english_prompt
    from services.shorts_service import SHORTS_STYLE_PRESETS, _NO_CJK, _NO_ANATOMY
    from services.imagen_service import _generate_flux

    try:
        flux_prompt = ensure_english_prompt(flux_prompt)
        style_mod   = SHORTS_STYLE_PRESETS.get(style, '')
        full_prompt = (
            flux_prompt +
            (f', {style_mod}' if style_mod else '') +
            ', 9:16 vertical frame, cinematic lighting' +
            _NO_CJK + _NO_ANATOMY
        )
        img_url, _ = _generate_flux(full_prompt, 'flux_preview', '1080x1920')
        return jsonify(ok=True, image_url=img_url, prompt_used=flux_prompt)
    except Exception as e:
        logger.error('[shorts/preview-image] %s', e)
        return jsonify(ok=False, message=f'이미지 생성 실패: {str(e)[:200]}')


# ─────────────────────────────────────────────────────────────
# 한글 씬 설명 → 영문 FLUX 이미지 프롬프트 변환
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/translate-prompt', methods=['POST'])
@login_required
def shorts_translate_prompt():
    """나레이션(한글) → 영문 FLUX 이미지 프롬프트 생성.

    body: { narration, overlay_title, overlay_body, style, role }
    """
    data          = request.get_json(force=True) or {}
    narration     = (data.get('narration')     or '').strip()
    overlay_title = (data.get('overlay_title') or '').strip()
    overlay_body  = (data.get('overlay_body')  or '').strip()
    style         = (data.get('style')         or 'realistic_banner').strip()
    role          = (data.get('role')          or 'hook').strip()

    if not narration and not overlay_title:
        return jsonify(ok=False, message='나레이션 또는 화면 타이틀이 없습니다.')

    from services.claude_service import generate_text
    from services.shorts_service import SHORTS_STYLE_PRESETS

    style_guide = SHORTS_STYLE_PRESETS.get(style, '')

    # 역할별 이미지 힌트
    role_hint = {
        'hook':           'problem/tension scene, relatable everyday situation',
        'empathy':        'emotional/frustrated person, empathetic atmosphere',
        'solution':       'product discovery moment, relief and positive change',
        'benefit':        'positive lifestyle result, aspirational scene',
        'cta':            'clean product display on surface, studio background — NO hands/fingers',
        'product_reveal': 'dramatic product reveal, spotlight effect',
    }.get(role, 'lifestyle scene')

    scene_text = narration or overlay_title
    if overlay_title:
        scene_text += f' / 화면 문구: {overlay_title}'
    if overlay_body:
        scene_text += f' / 자막: {overlay_body}'

    system = (
        'You are a FLUX image prompt expert for vertical (9:16) short-form video ads. '
        'Convert Korean scene descriptions into precise English image generation prompts. '
        'Output ONLY the English prompt, 60-80 words, no explanations.'
    )
    prompt = (
        f'Korean scene: {scene_text}\n'
        f'Scene role: {role_hint}\n'
        f'Style guide: {style_guide}\n\n'
        f'Write a FLUX image prompt (English only, 60-80 words, 9:16 vertical, '
        f'cinematic lighting, no text/watermark/CJK in image, no hands or fingers for cta scenes).'
    )

    try:
        flux_prompt = generate_text(system, prompt, max_tokens=200, model='claude-haiku-4-5').strip()
        # 따옴표·마크다운 제거
        flux_prompt = flux_prompt.strip('"\'`').strip()
        return jsonify(ok=True, flux_prompt=flux_prompt)
    except Exception as e:
        logger.error('[shorts/translate-prompt] %s', e)
        return jsonify(ok=False, message=f'번역 실패: {str(e)[:200]}')


# ─────────────────────────────────────────────────────────────
# 사용자 직접 업로드 이미지 → Supabase Storage → 공개 URL
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/upload-ref-image', methods=['POST'])
@login_required
def shorts_upload_ref_image():
    """사용자가 업로드한 이미지를 Supabase Storage에 저장 후 공개 URL 반환."""
    import uuid as _uuid
    supabase = current_app.supabase

    file = request.files.get('image')
    if not file:
        return jsonify(ok=False, message='이미지가 없습니다.')

    ext = 'jpg'
    if file.filename and '.' in file.filename:
        ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        ext = 'jpg'

    img_data  = file.read()
    file_path = f'tmp/user_uploads/{current_user.id}_{_uuid.uuid4().hex[:10]}.{ext}'
    mime      = file.mimetype or f'image/{ext}'

    try:
        supabase.storage.from_('maesil-files').upload(
            file_path, img_data,
            file_options={'content-type': mime, 'upsert': 'true'},
        )
        pub_url = supabase.storage.from_('maesil-files').get_public_url(file_path)
        return jsonify(ok=True, image_url=pub_url)
    except Exception as e:
        logger.error('[shorts/upload-ref-image] %s', e)
        return jsonify(ok=False, message=f'업로드 실패: {e}')


# ─────────────────────────────────────────────────────────────
# 내가 생성한 이미지 이력 (Step 5 기준 이미지 선택용)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/my-images', methods=['GET'])
@login_required
def shorts_my_images():
    """최근 생성 이미지 목록 반환 (이미지 생성 이력 + 상품 이미지).

    Step 5에서 기준 이미지를 기존 생성물에서 선택할 때 사용.
    """
    supabase = current_app.supabase

    # 1) 이미지 생성 이력 — output_data.image_url 이 있는 creation 타입만
    IMAGE_TYPES = (
        'image_generation', 'img_preview', 'img_ideogram',
        'bg_replace', 'bg_remove_adv', 'banner', 'banner_product', 'banner_text',
    )
    try:
        rows = supabase.table('creations').select(
            'id, creation_type, output_data, created_at'
        ).eq('user_id', current_user.id).eq('status', 'done').in_(
            'creation_type', list(IMAGE_TYPES)
        ).order('created_at', desc=True).limit(40).execute()
    except Exception as e:
        logger.error('[shorts/my-images] creations query: %s', e)
        rows = None

    images = []
    seen = set()
    for row in (rows.data or []):
        od = row.get('output_data') or {}
        url = od.get('image_url') or ''
        if url and url not in seen:
            seen.add(url)
            images.append({
                'url':   url,
                'type':  row.get('creation_type', ''),
                'date':  (row.get('created_at') or '')[:10],
            })

    # 2) 상품 이미지 — 브랜드 상관없이 유저 상품 전체
    try:
        prods = supabase.table('products').select(
            'name, images'
        ).eq('user_id', current_user.id).order('created_at', desc=True).limit(30).execute()
        for p in (prods.data or []):
            imgs = p.get('images') or []
            for img in (imgs if isinstance(imgs, list) else []):
                if isinstance(img, str) and img and img not in seen:
                    seen.add(img)
                    images.append({'url': img, 'type': 'product', 'label': p.get('name', '상품')})
    except Exception as e:
        logger.warning('[shorts/my-images] products query: %s', e)

    return jsonify(ok=True, images=images[:60])


# ─────────────────────────────────────────────────────────────
# FLUX 씬별 이미지 일괄 생성 (이미지 확인 단계용)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/scene-images', methods=['POST'])
@login_required
def shorts_scene_images():
    """5씬 FLUX 이미지 일괄 생성 → 이미지 확인 단계에서 사용자 검토.

    스토리보드 승인 후 호출. 영상 조립 전 이미지를 미리 확인/재생성할 수 있게 함.
    Returns: {"ok": true, "images": [{"idx": 0, "image_url": "..."}, ...]}
    """
    data   = request.get_json(force=True) or {}
    scenes = data.get('scenes') or []
    style  = (data.get('style') or 'realistic_banner').strip()

    if not scenes:
        return jsonify(ok=False, message='씬 데이터가 없습니다.')

    from services.shorts_service import SHORTS_STYLE_PRESETS, _NO_CJK, _NO_ANATOMY
    from services.imagen_service import _generate_flux
    from services.kling_service import ensure_english_prompt

    style_mod = SHORTS_STYLE_PRESETS.get(style, '')
    results = []

    for i, scene in enumerate(scenes):
        try:
            flux_prompt = ensure_english_prompt(scene.get('flux_prompt', '') or scene.get('narration', ''))
            full_prompt = (
                flux_prompt +
                (f', {style_mod}' if style_mod else '') +
                ', 9:16 vertical frame, cinematic lighting' +
                _NO_CJK + _NO_ANATOMY
            )
            img_url, _ = _generate_flux(full_prompt, 'flux_standard', '1080x1920')
            results.append({'idx': i, 'image_url': img_url, 'ok': True})
        except Exception as e:
            logger.error('[shorts/scene-images] 씬%d 이미지 생성 실패: %s', i, e)
            results.append({'idx': i, 'image_url': None, 'ok': False, 'error': str(e)[:100]})

    return jsonify(ok=True, images=results)


# ─────────────────────────────────────────────────────────────
# 영상 생성 (비동기 백그라운드)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/generate', methods=['POST'])
@login_required
def shorts_generate():
    """영상 생성 시작 → creation_id 즉시 반환, 백그라운드에서 진행 (300P)"""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    scenes            = data.get('scenes') or []
    style             = (data.get('style')             or 'realistic_banner').strip()
    brand_color       = (data.get('brand_color')       or '#e8355a').strip()
    voice_key         = (data.get('voice')             or 'female_natural').strip()
    tts_speed         = float(data.get('tts_speed') or 1.1)
    brand_id          = (data.get('brand_id')          or '').strip()
    bgm_volume        = float(data.get('bgm_volume') if data.get('bgm_volume') is not None else 0.20)
    bgm_volume        = max(0.0, min(1.0, bgm_volume))
    use_kling         = bool(data.get('use_kling', False))
    kling_model       = 'kling-v1'  # v1 고정 (비용 절감)
    product_image_url = (data.get('product_image_url') or '').strip() or None
    # 미리보기에서 승인된 기준 이미지 — 있으면 FLUX 재생성 생략
    ref_image_url     = (data.get('ref_image_url')     or '').strip() or None
    # FLUX 씬별 미리보기에서 사용자가 확인한 이미지 URL 목록 (있으면 재생성 생략)
    scene_images      = data.get('scene_images') or None  # list[str] | None

    if not scenes:
        return jsonify(ok=False, message='씬 데이터가 없습니다. 먼저 대본을 생성하세요.')

    # Kling 모드 → 실제 API 연결 사전 확인 (포인트 차감 전)
    if use_kling:
        from services.config_service import get_config as _gc
        from services.kling_service import verify_connection
        _ak = _gc('kling_access_key')
        _sk = _gc('kling_secret_key')
        _bu = _gc('kling_base_url') or 'https://api.klingai.com'
        ok, msg = verify_connection(_ak, _sk, _bu)
        if not ok:
            return jsonify(ok=False, message=f'Kling API 연결 실패: {msg}')

    creation_type = 'shorts_video_kling' if use_kling else 'shorts_video'
    cost = POINT_COSTS.get(creation_type, 300)

    from services.point_service import get_balance, use_points, InsufficientPoints
    balance = get_balance(current_user.id)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    creation_id = str(uuid.uuid4())
    model_used  = f'kling-{kling_model}+tts+ffmpeg' if use_kling else 'flux+tts+ffmpeg'
    try:
        _row = {
            'id': creation_id,
            'user_id': current_user.id,
            'brand_id': brand_id or None,
            'creation_type': creation_type,
            'input_data': {'style': style, 'voice': voice_key, 'scenes': scenes,
                           'use_kling': use_kling, 'kling_model': kling_model},
            'output_data': {'progress': 0, 'step': '준비 중'},
            'points_used': cost,
            'status': 'generating',
            'model_used': model_used,
            'created_at': now_kst().isoformat(),
        }
        if getattr(current_user, 'operator_id', None):
            _row['operator_id'] = current_user.operator_id
        supabase.table('creations').insert(_row).execute()
    except Exception as e:
        logger.warning('[shorts/generate] creation insert: %s', e)

    try:
        use_points(current_user.id, creation_type, creation_id)
    except InsufficientPoints:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message='포인트가 부족합니다.')

    supabase_url = current_app.config.get('SUPABASE_URL', '')
    supabase_key = (current_app.config.get('SUPABASE_SERVICE_KEY')
                    or current_app.config.get('SUPABASE_KEY', ''))

    if use_kling:
        from tasks.shorts_task import generate_kling_shorts_video
        generate_kling_shorts_video.delay(
            creation_id=creation_id,
            user_id=current_user.id,
            scenes=scenes,
            style=style,
            brand_color=brand_color,
            voice_key=voice_key,
            tts_speed=tts_speed,
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            bgm_volume=bgm_volume,
            kling_model=kling_model,
            product_image_url=product_image_url,
            ref_image_url=ref_image_url,
            scene_images=scene_images,         # 사전 확인된 씬별 이미지
        )
    else:
        from tasks.shorts_task import generate_shorts_video
        generate_shorts_video.delay(
            creation_id=creation_id,
            user_id=current_user.id,
            scenes=scenes,
            style=style,
            brand_color=brand_color,
            voice_key=voice_key,
            tts_speed=tts_speed,
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            bgm_volume=bgm_volume,
            scene_images=scene_images,
        )

    return jsonify(ok=True, creation_id=creation_id, cost=cost, engine='kling' if use_kling else 'flux')


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
# BGM 현황 조회
# ─────────────────────────────────────────────────────────────

@create_bp.route('/shorts/bgm_status', methods=['GET'])
@login_required
def shorts_bgm_status():
    """등록된 BGM 파일 현황 반환 (분위기별 카운트)."""
    from services.shorts_service import BGM_MOODS, _list_bgm_files, _BGM_ROOT
    import os
    status = {}
    for mood_key, meta in BGM_MOODS.items():
        files = _list_bgm_files(mood_key)
        status[mood_key] = {
            'label': meta['label'],
            'count': len(files),
            'files': [os.path.basename(f) for f in files],
        }
    total = sum(v['count'] for v in status.values())
    return jsonify(ok=True, moods=status, total=total,
                   bgm_root=os.path.normpath(_BGM_ROOT))


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
