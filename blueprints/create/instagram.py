"""인스타그램 콘텐츠 생성 — 5단계 위자드
  Step 1: 기본설정 (브랜드·상품·방향)
  Step 2: 소구포인트 선택 (Haiku 3안)
  Step 3: 캡션 초안 + 해시태그
  Step 4: 이미지 생성 (스타일 선택 + PIL 합성)
  Step 5: 완성본 (이미지 + 캡션 + 해시태그)
"""
import json
import logging
import re
import uuid

from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from blueprints.create import create_bp
from blueprints.create._base import (
    get_default_brand, get_brand_by_id, get_accessible_brands, run_text_generation,
)
from models import POINT_COSTS
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)

SIZE_MAP = {
    '1:1':  ('1080x1080', (1080, 1080)),
    '4:5':  ('1080x1350', (1080, 1350)),
    '9:16': ('1080x1920', (1080, 1920)),
}


# ── 헬퍼 ──────────────────────────────────────────────────────

def _accessible_products(supabase, brand_id=None) -> list:
    user = current_user
    try:
        if user.operator_id:
            q = supabase.table('products').select(
                'id,name,category,image_url,images'
            ).eq('operator_id', user.operator_id)
        else:
            q = supabase.table('products').select(
                'id,name,category,image_url,images'
            ).eq('user_id', user.id)
        if brand_id:
            q = q.eq('brand_id', brand_id)
        try:
            q = q.eq('is_active', True)
        except Exception:
            pass
        return (q.order('created_at', desc=True).execute()).data or []
    except Exception as e:
        logger.debug(f'[insta] products 조회 실패: {e}')
        return []


def _product_images(p: dict) -> list[str]:
    imgs = list(p.get('images') or [])
    if p.get('image_url') and p['image_url'] not in imgs:
        imgs.insert(0, p['image_url'])
    return [u for u in imgs if u]


def _get_product(supabase, product_id: str) -> dict | None:
    if not product_id:
        return None
    try:
        res = supabase.table('products').select('*').eq('id', product_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception:
        return None


# ── 라우트 ────────────────────────────────────────────────────

def _recent_instagram_creations(supabase, user_id: str, brand_id: str | None,
                                limit: int = 30) -> list[dict]:
    """최근 인스타그램 생성 이력 (연속/시리즈 dropdown 용)."""
    try:
        q = (supabase.table('creations')
             .select('id,brand_id,output_data,input_data,created_at')
             .eq('user_id', user_id)
             .eq('creation_type', 'instagram')
             .eq('status', 'done')
             .order('created_at', desc=True)
             .limit(limit))
        if brand_id:
            q = q.eq('brand_id', brand_id)
        rows = q.execute().data or []
    except Exception as e:
        logger.debug(f'[insta] recent creations 실패: {e}')
        return []

    out = []
    for r in rows:
        inp = r.get('input_data') or {}
        out_data = r.get('output_data') or {}
        title = (out_data.get('caption') or inp.get('direction') or '')[:60] or '(제목 없음)'
        out.append({
            'id':         r.get('id'),
            'title':      title,
            'created_at': r.get('created_at', ''),
        })
    return out


@create_bp.route('/instagram', methods=['GET'])
@login_required
def instagram():
    supabase = current_app.supabase
    brands = get_accessible_brands(supabase)
    if not brands:
        flash('먼저 브랜드 프로필을 등록해 주세요.', 'warning')
        return redirect(url_for('main.onboarding'))
    default_brand = get_default_brand(supabase)
    products      = _accessible_products(supabase,
                                         brand_id=default_brand['id'] if default_brand else None)
    products_images_map = {p['id']: _product_images(p) for p in products}
    recent_instagrams   = _recent_instagram_creations(
        supabase, current_user.id, default_brand['id'] if default_brand else None)

    return render_template('create/instagram.html',
                           brands=brands,
                           default_brand=default_brand,
                           products=products,
                           products_images_map=products_images_map,
                           recent_instagrams=recent_instagrams)


@create_bp.route('/instagram/products', methods=['GET'])
@login_required
def instagram_products():
    supabase  = current_app.supabase
    brand_id  = request.args.get('brand_id', '').strip()
    products  = _accessible_products(supabase, brand_id=brand_id or None)
    return jsonify({
        'ok': True,
        'products': [
            {'id': p['id'], 'name': p['name'],
             'category': p.get('category', ''),
             'image_url': p.get('image_url') or '',
             'images': _product_images(p)}
            for p in products
        ],
    })


@create_bp.route('/instagram/ref-preview', methods=['GET'])
@login_required
def instagram_ref_preview():
    """시리즈/변형 모드에서 참조 게시물 발췌 미리보기."""
    supabase = current_app.supabase
    ref_id   = request.args.get('id', '').strip()
    if not ref_id:
        return jsonify(ok=False, message='id 필요')
    try:
        r = supabase.table('creations').select(
            'id,output_data,input_data'
        ).eq('id', ref_id).eq('user_id', current_user.id).limit(1).execute()
        if not r.data:
            return jsonify(ok=False, message='게시물을 찾을 수 없습니다.')
        row = r.data[0]
        inp      = row.get('input_data') or {}
        out_data = row.get('output_data') or {}
        title    = (out_data.get('caption') or inp.get('direction') or '')[:60] or '(제목 없음)'
        caption  = out_data.get('caption') or ''
        excerpt  = caption[:400] if caption else (inp.get('direction') or '')[:400]
        return jsonify(ok=True, title=title, excerpt=excerpt)
    except Exception as e:
        logger.debug(f'[insta] ref-preview 실패: {e}')
        return jsonify(ok=False, message=str(e))


# ─────────────────────────────────────────────────────────────
# Step 2 — 소구포인트 (Instagram 특화)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/instagram/angles', methods=['POST'])
@login_required
def instagram_angles():
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    brand_id   = (data.get('brand_id')   or '').strip()
    product_id = (data.get('product_id') or '').strip()
    direction  = (data.get('direction')  or '').strip()

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id) if product_id else None

    from services.claude_service import build_brand_context, generate_text
    brand_ctx = build_brand_context(brand, product)

    system = (
        '당신은 인스타그램 마케팅 전문가입니다. '
        '브랜드와 상품 정보를 분석해 인스타그램 포스트에 적합한 소구포인트(핵심 방향) 시안 3개를 제안합니다. '
        '결과는 순수 JSON 배열만 출력하세요.'
    )
    dir_line = f'\n- 게시 방향: {direction}' if direction else ''
    prompt   = f"""다음 정보를 바탕으로 인스타그램 포스트 소구포인트 시안 3개를 JSON으로 생성하세요.

[브랜드·상품 정보]
{brand_ctx}{dir_line}

인스타그램에서 실제로 반응 오는 5가지 스토리 아크 유형:
- 고민공감형: 공감 → 심화 → 전환 → 해결 → 희망 (감성·공감 위주)
- 문제해결형: 문제 → 고통 → 발견 → 결과 → CTA (솔루션 제시)
- 정보제공형: 후킹 질문 → 정보1 → 정보2 → 정보3 → 정리 (교육형)
- 사건스토리형: 사건 → 전개 → 클라이막스 → 해결 → 반전 (스토리텔링)
- 유머공감형: 웃긴상황 → 공감 → 더웃김 → 반전 → 제품연결 (유머)

각 시안은 이 중 하나의 스토리 아크를 선택해 맞춤 제안하세요.

각 시안 필드:
- id: "angle_1" ~ "angle_3"
- title: 방향 제목 (8자 이내)
- hook: 첫 줄 후킹 문구 (20자 이내)
- target: 타겟 (15자 이내)
- tone: 톤 (감성형/유머형/정보형/라이프스타일형 중 택1)
- story_arc: 위 5가지 중 가장 어울리는 스토리 아크 유형명 (예: "고민공감형")
- arc_flow: 이 아크의 컷 흐름 요약 (예: "공감→심화→전환→해결→희망", 화살표로 연결)
- image_vibe: 이미지 분위기 (한글, 1문장)
- key_message: 핵심 메시지 2~3줄 (문자열 배열)

순수 JSON 배열만 출력:
[{{"id":"angle_1","title":"...","hook":"...","target":"...","tone":"...","story_arc":"...","arc_flow":"...","image_vibe":"...","key_message":["...","..."]}},...]\n"""

    try:
        raw   = generate_text(system, prompt, max_tokens=1200, model='claude-haiku-4-5-20251001')
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        s, e  = clean.find('['), clean.rfind(']') + 1
        if s >= 0 and e > s:
            clean = clean[s:e]
        angles = json.loads(clean)
        if not isinstance(angles, list) or not angles:
            raise ValueError('empty')
        return jsonify(ok=True, angles=angles[:3])
    except Exception as ex:
        logger.error(f'[insta/angles] {ex}')
        return jsonify(ok=False, message=f'소구포인트 생성 실패: {ex}')


# ─────────────────────────────────────────────────────────────
# Step 3 — 캡션 + 해시태그
# ─────────────────────────────────────────────────────────────

@create_bp.route('/instagram/generate', methods=['POST'])
@login_required
def instagram_generate():
    supabase   = current_app.supabase
    data       = request.get_json(force=True) or {}

    brand_id   = (data.get('brand_id')   or '').strip()
    product_id = (data.get('product_id') or '').strip()
    direction  = (data.get('direction')  or '').strip()
    angle      = data.get('angle') or {}

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id) if product_id else None

    from services.claude_service import build_brand_context, generate_text
    brand_ctx = build_brand_context(brand, product)

    angle_title = angle.get('title', '') if isinstance(angle, dict) else str(angle)
    angle_hook  = angle.get('hook',  '') if isinstance(angle, dict) else ''
    angle_tone  = angle.get('tone',  '') if isinstance(angle, dict) else ''

    system = (
        '당신은 인스타그램 마케팅 전문가입니다. '
        '브랜드 톤앤매너에 맞는 캡션과 해시태그를 작성합니다. '
        '절대 광고스럽지 않게, 독자가 공감하고 저장하고 싶게 작성하세요.'
    )
    prompt = f"""아래 정보로 인스타그램 캡션 3가지 버전 + 해시태그를 작성하세요.

[브랜드·상품 정보]
{brand_ctx}

[선택된 소구포인트]
- 방향: {angle_title}
- 첫 줄 후킹: {angle_hook}
- 톤: {angle_tone}
- 게시 방향: {direction}

## 캡션 — 짧은 버전 (3~5줄)
[첫 줄 = 시선 잡는 후킹. 이모지 자연스럽게 활용. 마지막 줄 = 부드러운 행동 유도]

## 캡션 — 보통 버전 (6~9줄)
[스토리텔링 + 제품 자연 언급 + CTA]

## 캡션 — 긴 버전 (10~14줄)
[감성·경험·공감 스토리 + 정보 + CTA]

## 해시태그 (30개)
인기 태그 15개 + 틈새 태그 15개:
#태그1 #태그2 ... (한 줄로)

## 최적 게시 시간
[요일/시간대 + 이유 1줄]"""

    input_data = {'direction': direction, 'angle_title': angle_title}
    result     = run_text_generation(
        'instagram', brand, input_data, system, prompt,
        ledger_note='인스타그램 캡션 + 해시태그',
        max_tokens=2000,
    )
    return jsonify(result)


# ─────────────────────────────────────────────────────────────
# Step 4 — N컷 스토리 플랜 생성 (Haiku)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/instagram/story-plan', methods=['POST'])
@login_required
def instagram_story_plan():
    """스타일 + 장 수 + 소구포인트 → 패널별 장면·프롬프트·텍스트 배열"""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    style      = (data.get('style')      or 'realistic_banner').strip()
    quantity   = min(max(int(data.get('quantity') or 5), 1), 9)
    brand_id   = (data.get('brand_id')   or '').strip()
    product_id = (data.get('product_id') or '').strip()
    angle      = data.get('angle') or {}
    direction  = (data.get('direction')  or '').strip()
    img_size   = (data.get('size')       or '1:1').strip()

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id) if product_id else None

    from services.claude_service import build_brand_context, generate_text
    brand_ctx   = build_brand_context(brand, product)
    angle_title = angle.get('title', '')     if isinstance(angle, dict) else ''
    angle_vibe  = angle.get('image_vibe', '') if isinstance(angle, dict) else ''
    angle_hook  = angle.get('hook', '')      if isinstance(angle, dict) else ''
    story_arc   = angle.get('story_arc', '') if isinstance(angle, dict) else ''
    arc_flow    = angle.get('arc_flow', '')  if isinstance(angle, dict) else ''

    # story_arc 없으면 기본 추론
    if not story_arc:
        tone = (angle.get('tone', '') if isinstance(angle, dict) else '').lower()
        if '유머' in tone:
            story_arc, arc_flow = '유머공감형', '웃긴상황→공감→더웃김→반전→제품연결'
        elif '정보' in tone:
            story_arc, arc_flow = '정보제공형', '후킹질문→정보1→정보2→정보3→정리'
        else:
            story_arc, arc_flow = '고민공감형', '공감→심화→전환→해결→희망'

    STYLE_INFO = {
        'realistic_banner': {
            'name':       '실사 라이프스타일 배너',
            'img_guide':  '실사 사진 스타일. 사람/라이프스타일 장면. 텍스트 포함 금지. 인물이 등장할 경우 기본적으로 동아시아인(Korean/East Asian appearance)으로 묘사하세요.',
            'text_field': 'title(메인 한글 문구 15자 이내), subtitle(서브 20자 이내, 없으면 빈 문자열)',
            'story_hint': '각 컷은 홍보 스토리텔링: 공감→문제→해결→제품→CTA 흐름으로',
        },
        'webtoon': {
            'name':       '웹툰 만화 컷',
            'img_guide':  '한국 웹툰 스타일. 귀엽고 디테일한 캐릭터. 말풍선 공간 확보. 텍스트 배경 없이. 캐릭터는 동아시아인(Korean/East Asian features) 외모로 묘사하세요.',
            'text_field': 'dialogue1(첫 번째 말풍선 20자 이내), dialogue2(두 번째 말풍선 20자 이내, 없으면 빈 문자열)',
            'story_hint': '연속 만화 스토리: 도입→문제→발견→해결→마무리 형식으로 자연스럽게 연결',
        },
        'typography': {
            'name':       '타이포그래피 카드',
            'img_guide':  '텍스트 중심 감성 디자인 카드. 브랜드 컬러 배경. 한글 텍스트 직접 포함.',
            'text_field': 'title(메인 15자 이내), subtitle(서브 20자 이내)',
            'story_hint': '메시지 카드 시리즈: 각 카드가 하나의 메시지를 전달. 시리즈로 읽히도록',
        },
    }
    info = STYLE_INFO.get(style, STYLE_INFO['realistic_banner'])

    # arc_flow → quantity에 맞게 역할 배분
    arc_stages = [s.strip() for s in arc_flow.split('→') if s.strip()] if arc_flow else []
    if arc_stages and len(arc_stages) != quantity:
        # 스테이지 수를 quantity에 맞게 조정
        if len(arc_stages) > quantity:
            arc_stages = arc_stages[:quantity]
        else:
            while len(arc_stages) < quantity:
                arc_stages.append('마무리')
    arc_hint = ' → '.join(arc_stages) if arc_stages else ''

    system = '당신은 인스타그램 카드뉴스·웹툰 스토리 작가 겸 AI 이미지 프롬프트 엔지니어입니다. 순수 JSON 배열만 출력하세요.'
    prompt = f"""인스타그램 {quantity}컷 {info['name']} 구성안을 JSON 배열로 만드세요.

[브랜드·상품]
{brand_ctx}

[소구포인트]
- 방향: {angle_title}
- 이미지 분위기: {angle_vibe}
- 후킹 문구: {angle_hook}
- 게시 방향: {direction}

[스토리 아크: {story_arc}]
컷 흐름: {arc_hint or info['story_hint']}
각 컷이 이 흐름대로 자연스럽게 연결되도록 구성하세요.
스토리는 처음부터 끝까지 하나의 완결된 이야기로 흘러야 합니다.

[이미지 스타일]
{info['name']}: {info['img_guide']}

[비율] {img_size}

[출력 — 정확히 {quantity}개 JSON 배열]
각 패널 필드:
- panel: 번호 (1~{quantity})
- role: 이 컷의 역할 — 위 흐름 단계명 그대로 (8자 이내)
- scene_ko: 이 장면 한국어 요약 (15자 이내)
- flux_prompt: 영문 FLUX 프롬프트 (50~80단어, 이 패널 장면에 맞게 구체적으로. 직전 패널과 달라야 함)
- {info['text_field']}
- title, subtitle, dialogue1, dialogue2 — 해당 스타일에 쓰지 않는 필드는 반드시 빈 문자열

순수 JSON 배열만:
[{{"panel":1,"role":"공감","scene_ko":"...","flux_prompt":"...","title":"...","subtitle":"...","dialogue1":"","dialogue2":""}},...]"""

    try:
        raw    = generate_text(system, prompt,
                               max_tokens=quantity * 350,
                               model='claude-haiku-4-5-20251001')
        clean  = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        s, e   = clean.find('['), clean.rfind(']') + 1
        if s >= 0 and e > s:
            clean = clean[s:e]
        panels = json.loads(clean)
        if not isinstance(panels, list) or not panels:
            raise ValueError('empty')
        return jsonify(ok=True, panels=panels[:quantity])
    except Exception as ex:
        logger.error(f'[insta/story-plan] {ex}')
        return jsonify(ok=False, message=f'스토리 구성 생성 실패: {ex}')


# ─────────────────────────────────────────────────────────────
# Step 4 — 이미지 프롬프트 자동 생성 (단일, Haiku)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/instagram/image-prompt', methods=['POST'])
@login_required
def instagram_image_prompt():
    """스타일 + 소구포인트 → FLUX/Ideogram 프롬프트 + 추천 텍스트 자동 생성"""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    style      = (data.get('style')      or 'realistic_banner').strip()
    brand_id   = (data.get('brand_id')   or '').strip()
    product_id = (data.get('product_id') or '').strip()
    angle      = data.get('angle') or {}
    direction  = (data.get('direction')  or '').strip()
    img_size   = (data.get('size')       or '1:1').strip()

    brand   = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')
    product = _get_product(supabase, product_id) if product_id else None

    from services.claude_service import build_brand_context, generate_text
    brand_ctx   = build_brand_context(brand, product)
    angle_title = angle.get('title', '') if isinstance(angle, dict) else ''
    angle_vibe  = angle.get('image_vibe', '') if isinstance(angle, dict) else ''
    angle_hook  = angle.get('hook',  '') if isinstance(angle, dict) else ''

    STYLE_GUIDE = {
        'realistic_banner': '실사 라이프스타일 사진 스타일. 제품 없이 분위기/감성 장면. 텍스트 없이. 인물이 등장할 경우 기본적으로 East Asian/Korean appearance으로.',
        'webtoon':          '한국 웹툰 스타일. 귀여운 캐릭터·장면. 텍스트 없이. 말풍선 공간 남기기. 캐릭터는 East Asian/Korean features.',
        'typography':       '타이포그래피 디자인 카드. 한글 텍스트를 이미지에 직접 포함. 브랜드 컬러 활용.',
    }
    style_guide = STYLE_GUIDE.get(style, STYLE_GUIDE['realistic_banner'])

    TEXT_FIELD_GUIDE = {
        'realistic_banner': '이미지 하단 배너에 들어갈 텍스트: title(메인 한글 문구), subtitle(서브 한글 문구)',
        'webtoon':          '말풍선 대사: dialogue1(첫 번째 대사), dialogue2(두 번째 대사, 선택)',
        'typography':       '이미지에 들어갈 한글 텍스트: title(메인), subtitle(서브) — Ideogram이 이미지에 직접 렌더링',
    }

    system = '당신은 AI 이미지 프롬프트 엔지니어입니다. 순수 JSON만 출력하세요.'
    prompt = f"""인스타그램 이미지 프롬프트와 추천 텍스트를 JSON으로 생성하세요.

[브랜드·상품]
{brand_ctx}

[소구포인트]
- 방향: {angle_title}
- 이미지 분위기: {angle_vibe}
- 후킹 문구: {angle_hook}
- 게시 방향: {direction}

[이미지 스타일]
{style_guide}

[출력 형식 — 순수 JSON]
{{
  "flux_prompt": "영문 이미지 생성 프롬프트 (60~90단어, 구체적·상세)",
  "title":     "이미지 안 메인 한글 문구 (15자 이내)",
  "subtitle":  "이미지 안 서브 한글 문구 (20자 이내, 선택)",
  "dialogue1": "첫 번째 말풍선 대사 (웹툰용, 20자 이내)",
  "dialogue2": "두 번째 말풍선 대사 (웹툰용, 선택)"
}}

스타일: {style}
비율: {img_size}"""

    try:
        raw   = generate_text(system, prompt, max_tokens=500, model='claude-haiku-4-5-20251001')
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        s, e  = clean.find('{'), clean.rfind('}') + 1
        if s >= 0 and e > s:
            clean = clean[s:e]
        result = json.loads(clean)
        return jsonify(ok=True, **result)
    except Exception as ex:
        logger.error(f'[insta/image-prompt] {ex}')
        return jsonify(ok=False, message=f'프롬프트 생성 실패: {ex}')


# ─────────────────────────────────────────────────────────────
# Step 4 — 이미지 생성 (FLUX / Ideogram + PIL)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/instagram/image-generate', methods=['POST'])
@login_required
def instagram_image_generate():
    """이미지 생성 — 스타일별 FLUX/Ideogram + PIL 합성"""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}

    style        = (data.get('style')        or 'realistic_banner').strip()
    flux_prompt  = (data.get('flux_prompt')  or '').strip()
    img_size     = (data.get('size')         or '1:1').strip()
    brand_color  = (data.get('brand_color')  or '#e8355a').strip()
    title        = (data.get('title')        or '').strip()
    subtitle     = (data.get('subtitle')     or '').strip()
    dialogue1    = (data.get('dialogue1')    or '').strip()
    dialogue2    = (data.get('dialogue2')    or '').strip()

    if not flux_prompt:
        return jsonify(ok=False, message='이미지 프롬프트를 입력하세요.')

    flux_size_str, pil_size = SIZE_MAP.get(img_size, SIZE_MAP['1:1'])

    # 비용
    cost_key = 'img_ideogram' if style == 'typography' else 'img_preview'
    cost     = POINT_COSTS.get(cost_key, 50)

    # 포인트 확인
    from services.point_service import get_balance, use_points, InsufficientPoints
    balance = get_balance(current_user.id)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트 부족 (필요: {cost}P, 잔액: {balance}P)')

    creation_id = str(uuid.uuid4())
    try:
        supabase.table('creations').insert({
            'id':            creation_id,
            'user_id':       current_user.id,
            'creation_type': cost_key,
            'input_data':    {'prompt': flux_prompt, 'style': style, 'size': img_size},
            'output_data':   {},
            'points_used':   cost,
            'status':        'generating',
            'model_used':    'ideogram' if style == 'typography' else 'flux_schnell',
            'created_at':    now_kst().isoformat(),
        }).execute()
    except Exception as e:
        logger.warning(f'[insta img] creation insert: {e}')

    try:
        from services.imagen_service import upload_to_supabase

        translated_prompt = ''
        bg_url = None

        if style == 'typography':
            # Ideogram — 한글 텍스트 포함 프롬프트를 직접 전달
            from services.imagen_service import _generate_ideogram
            img_url, _ = _generate_ideogram(flux_prompt, flux_size_str)
            final_url = upload_to_supabase(img_url, current_user.id,
                                           f'insta_typo_{creation_id[:8]}.jpg')

        elif style == 'webtoon':
            from services.imagen_service import _generate_flux
            from services.instagram_service import create_webtoon_image
            bg_url, translated_prompt = _generate_flux(flux_prompt, 'flux_preview', flux_size_str)
            dialogues = [d for d in [dialogue1, dialogue2] if d]
            data_url  = create_webtoon_image(bg_url, dialogues, pil_size)
            final_url = upload_to_supabase(data_url, current_user.id,
                                           f'insta_webtoon_{creation_id[:8]}.jpg')

        else:  # realistic_banner (기본)
            from services.imagen_service import _generate_flux
            from services.instagram_service import create_banner_image
            bg_url, translated_prompt = _generate_flux(flux_prompt, 'flux_preview', flux_size_str)
            texts     = [t for t in [title, subtitle] if t]
            data_url  = create_banner_image(bg_url, texts, brand_color, pil_size)
            final_url = upload_to_supabase(data_url, current_user.id,
                                           f'insta_banner_{creation_id[:8]}.jpg')

        # 포인트 차감
        use_points(current_user.id, cost_key, creation_id)

        supabase.table('creations').update({
            'output_data': {'image_url': final_url},
            'status':      'done',
        }).eq('id', creation_id).execute()

        return jsonify(ok=True, image_url=final_url, base_image_url=bg_url, cost=cost,
                       creation_id=creation_id, translated_prompt=translated_prompt or None)

    except Exception as ex:
        logger.error(f'[insta/image-generate] {ex}')
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return jsonify(ok=False, message=f'이미지 생성 실패: {ex}')


# ─────────────────────────────────────────────────────────────
# 재합성 — 포인트 소모 없이 PIL 재합성만
# ─────────────────────────────────────────────────────────────

@create_bp.route('/instagram/recomposite-banner', methods=['POST'])
@login_required
def instagram_recomposite_banner():
    """base_image_url + 텍스트 + 위치 → PIL 재합성 (포인트 소모 없음)"""
    data         = request.get_json(force=True) or {}
    base_url     = (data.get('base_image_url') or '').strip()
    title        = (data.get('title')          or '').strip()
    subtitle     = (data.get('subtitle')       or '').strip()
    brand_color  = (data.get('brand_color')    or '#e8355a').strip()
    img_size     = (data.get('size')           or '1:1').strip()
    text_gravity = (data.get('text_gravity')   or 'bottom-left').strip()
    text_scale   = float(data.get('text_scale') or 1.0)

    if not base_url:
        return jsonify(ok=False, message='base_image_url이 필요합니다.')

    from services.instagram_service import create_banner_image
    from services.imagen_service import upload_to_supabase
    import uuid

    _, pil_size = SIZE_MAP.get(img_size, SIZE_MAP['1:1'])
    texts = [t for t in [title, subtitle] if t]
    try:
        data_url  = create_banner_image(base_url, texts, brand_color, pil_size, text_gravity, text_scale)
        filename  = f'insta_banner_r_{uuid.uuid4().hex[:8]}.jpg'
        final_url = upload_to_supabase(data_url, current_user.id, filename)
        return jsonify(ok=True, image_url=final_url)
    except Exception as e:
        logger.error(f'[recomposite-banner] {e}')
        return jsonify(ok=False, message=str(e))


@create_bp.route('/instagram/recomposite-webtoon', methods=['POST'])
@login_required
def instagram_recomposite_webtoon():
    """base_image_url + 대사 + 레이아웃 → PIL 재합성 (포인트 소모 없음)"""
    data          = request.get_json(force=True) or {}
    base_url      = (data.get('base_image_url') or '').strip()
    dialogue1     = (data.get('dialogue1')      or '').strip()
    dialogue2     = (data.get('dialogue2')      or '').strip()
    img_size      = (data.get('size')           or '1:1').strip()
    bubble_layout = (data.get('bubble_layout')  or 'default').strip()

    if not base_url:
        return jsonify(ok=False, message='base_image_url이 필요합니다.')

    from services.instagram_service import create_webtoon_image
    from services.imagen_service import upload_to_supabase
    import uuid

    _, pil_size  = SIZE_MAP.get(img_size, SIZE_MAP['1:1'])
    dialogues    = [d for d in [dialogue1, dialogue2] if d]
    try:
        data_url  = create_webtoon_image(base_url, dialogues, pil_size, bubble_layout)
        filename  = f'insta_webtoon_r_{uuid.uuid4().hex[:8]}.jpg'
        final_url = upload_to_supabase(data_url, current_user.id, filename)
        return jsonify(ok=True, image_url=final_url)
    except Exception as e:
        logger.error(f'[recomposite-webtoon] {e}')
        return jsonify(ok=False, message=str(e))
