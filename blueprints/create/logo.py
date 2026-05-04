"""브랜드 로고 생성 — Ideogram V3 (한글 네이티브 렌더링)"""
import logging
import uuid

from flask import render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from blueprints.create import create_bp
from blueprints.create._base import get_accessible_brands, get_default_brand, get_brand_by_id
from models import POINT_COSTS
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


@create_bp.route('/logo')
@login_required
def logo():
    supabase = current_app.supabase
    brands   = get_accessible_brands(supabase)
    default  = get_default_brand(supabase)
    return render_template('create/logo.html', brands=brands, default_brand=default)


@create_bp.route('/logo/generate', methods=['POST'])
@login_required
def logo_generate():
    """Ideogram V3으로 로고 시안 3개 생성 (800P)"""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    brand_id     = (data.get('brand_id')     or '').strip()
    brand_name   = (data.get('brand_name')   or '').strip()
    brand_name_ko= (data.get('brand_name_ko')or '').strip()
    tagline      = (data.get('tagline')      or '').strip()
    logo_style   = (data.get('logo_style')   or 'combination').strip()
    vibe         = (data.get('vibe')         or 'modern_minimal').strip()
    primary_color= (data.get('primary_color')or '').strip()
    extra        = (data.get('extra')        or '').strip()

    if not brand_name and not brand_name_ko:
        return jsonify(ok=False, message='브랜드명을 입력해주세요.')

    cost = POINT_COSTS.get('logo', 800)
    from services.point_service import get_balance, use_points, InsufficientPoints
    balance = get_balance(current_user.id)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    creation_id = str(uuid.uuid4())
    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)

    try:
        supabase.table('creations').insert({
            'id': creation_id,
            'user_id': current_user.id,
            'brand_id': brand['id'] if brand else None,
            'creation_type': 'logo',
            'input_data': data,
            'output_data': {},
            'points_used': cost,
            'status': 'generating',
            'model_used': 'ideogram-v3',
            'created_at': now_kst().isoformat(),
        }).execute()
    except Exception as e:
        logger.warning('[logo] creation insert: %s', e)

    try:
        from services.config_service import get_config
        from services.claude_service import generate_text
        import requests as _req

        api_key = get_config('ideogram_api_key')
        if not api_key:
            raise ValueError('Ideogram API Key가 설정되지 않았습니다. 어드민 → 시스템 설정에서 등록하세요.')

        # ── Claude Haiku로 최적화된 Ideogram 프롬프트 생성 ──
        STYLE_DESC = {
            'wordmark':    'wordmark logo — brand name as stylized typography only, no icon',
            'lettermark':  'lettermark logo — initials only, bold geometric lettering',
            'emblem':      'emblem logo — icon + text inside a badge or seal shape',
            'combination': 'combination mark — icon symbol alongside brand name text',
            'mascot':      'mascot logo — friendly character representing the brand',
        }
        VIBE_DESC = {
            'modern_minimal': 'modern minimalist, clean lines, flat design, sans-serif',
            'vintage_classic': 'vintage classic, retro serif typography, distressed texture',
            'cute_friendly':  'cute friendly, rounded shapes, soft colors, approachable',
            'tech_bold':      'tech bold, geometric sharp shapes, futuristic, high contrast',
            'natural_warm':   'natural warm, organic shapes, earth tones, handcrafted feel',
            'luxury_premium': 'luxury premium, elegant thin lines, gold accent, sophisticated',
        }

        name_display = f'"{brand_name_ko}"' if brand_name_ko else f'"{brand_name}"'
        eng_name     = f'"{brand_name}"' if brand_name else ''

        color_hint = f', color palette: {primary_color}' if primary_color else ''
        tagline_hint = f', tagline: "{tagline}"' if tagline else ''
        extra_hint   = f', {extra}' if extra else ''

        style_str = STYLE_DESC.get(logo_style, STYLE_DESC['combination'])
        vibe_str  = VIBE_DESC.get(vibe, VIBE_DESC['modern_minimal'])

        # 3가지 변형 프롬프트를 Claude가 생성
        system = '당신은 Ideogram 로고 프롬프트 전문가입니다. 순수 JSON만 출력하세요.'
        prompt = f"""브랜드 {name_display}{f' ({eng_name})' if eng_name else ''}의 로고 시안 3가지를 위한 Ideogram V3 프롬프트를 생성하세요.

스타일: {style_str}
분위기: {vibe_str}{color_hint}{tagline_hint}{extra_hint}

규칙:
- 브랜드명 한글 {name_display}이 로고에 명확히 표시되어야 함
- transparent background, vector style, professional logo design
- 3개 각각 다른 구도/스타일 변형 (같은 방향 반복 금지)
- 순수 JSON 배열 출력

[출력]
["프롬프트1 (영문, 50~80자)", "프롬프트2", "프롬프트3"]"""

        import re, json
        raw   = generate_text(system, prompt, max_tokens=600, model='claude-haiku-4-5-20251001')
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        s, e  = clean.find('['), clean.rfind(']') + 1
        prompts = json.loads(clean[s:e]) if s >= 0 and e > s else [clean]

        # ── Ideogram API — 시안 3개 생성 ──
        logo_urls = []
        for p in prompts[:3]:
            full_prompt = (
                f'{p}, brand name text: {name_display}'
                f'{tagline_hint}, transparent background, '
                'professional logo, vector style, high quality'
            )
            resp = _req.post(
                'https://api.ideogram.ai/generate',
                headers={'Api-Key': api_key, 'Content-Type': 'application/json'},
                json={
                    'image_request': {
                        'prompt': full_prompt,
                        'model': 'V_3',
                        'magic_prompt_option': 'OFF',
                        'aspect_ratio': 'ASPECT_1_1',
                        'style_type': 'DESIGN',
                        'negative_prompt': 'blurry, low quality, watermark, distorted text',
                    }
                },
                timeout=60,
            )
            resp.raise_for_status()
            url = resp.json()['data'][0]['url']
            logo_urls.append(url)

        use_points(current_user.id, 'logo', creation_id)

        supabase.table('creations').update({
            'output_data': {'logo_urls': logo_urls},
            'status': 'done',
        }).eq('id', creation_id).execute()

        return jsonify(ok=True, logo_urls=logo_urls, creation_id=creation_id, cost=cost)

    except InsufficientPoints:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message='포인트가 부족합니다.')
    except Exception as e:
        logger.error('[logo] generate error: %s', e)
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message=f'로고 생성 실패: {e}')
