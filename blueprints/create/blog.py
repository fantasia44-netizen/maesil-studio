"""블로그 포스트 생성 — 5단계 위자드 (소구포인트→글→이미지→완성본)."""
import json
import logging
import re
from flask import render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from blueprints.create import create_bp
from blueprints.create._base import (
    get_default_brand, get_brand_by_id, get_accessible_brands,
    run_text_generation,
)
from models import (
    BLOG_LENGTH_COSTS, BLOG_ANGLE_OPTIONS, RELATION_MODE_OPTIONS,
    PRODUCT_CATEGORY_OPTIONS, get_blog_cost,
)
from services.regulatory import combine_avoid_words, append_disclaimer

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────

def _accessible_products(supabase, brand_id: str | None = None) -> list:
    """현재 사용자가 접근 가능한 상품. brand_id 지정 시 해당 브랜드만."""
    user = current_user
    try:
        if user.operator_id:
            q = supabase.table('products').select(
                'id,name,category,price,avoid_words,brand_id,image_url,images'
            ).eq('operator_id', user.operator_id)
        else:
            q = supabase.table('products').select(
                'id,name,category,price,avoid_words,brand_id,image_url,images'
            ).eq('user_id', user.id)
        if brand_id:
            q = q.eq('brand_id', brand_id)
        # 활성만
        try:
            q = q.eq('is_active', True)
        except Exception:
            pass  # is_active 없는 환경 대비
        res = q.order('created_at', desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.debug(f'[blog] products 조회 실패: {e}')
        return []


def _get_product(supabase, product_id: str) -> dict | None:
    if not product_id:
        return None
    try:
        res = supabase.table('products').select('*').eq('id', product_id).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception:
        return None


def _recent_blog_creations(supabase, user_id: str, brand_id: str | None,
                           limit: int = 30) -> list[dict]:
    """최근 블로그 생성 이력 (회피용 + 시리즈 dropdown 옵션)."""
    try:
        q = (supabase.table('creations')
             .select('id,brand_id,output_data,input_data,created_at')
             .eq('user_id', user_id)
             .eq('creation_type', 'blog')
             .eq('status', 'done')
             .order('created_at', desc=True)
             .limit(limit))
        if brand_id:
            q = q.eq('brand_id', brand_id)
        res = q.execute()
        rows = res.data or []
    except Exception as e:
        logger.debug(f'[blog] recent creations 실패: {e}')
        return []

    out = []
    for r in rows:
        title = _extract_title(r.get('output_data', {}))
        inp = r.get('input_data') or {}
        out.append({
            'id':        r.get('id'),
            'title':     title or (inp.get('topic') or ''),
            'topic':     inp.get('topic', ''),
            'keyword':   inp.get('keyword', ''),
            'angle':     inp.get('angle', ''),
            'created_at': r.get('created_at', ''),
        })
    return out


_TITLE_RE = re.compile(r'^\s*(?:[1-3]\.)\s*(.+?)\s*$', re.M)


def _extract_title(output_data: dict) -> str:
    """첫 제목 후보를 추출."""
    text = (output_data or {}).get('text') or ''
    if not text:
        return ''
    # ## 제목 후보 섹션 찾기
    sec_idx = text.find('제목 후보')
    if sec_idx >= 0:
        after = text[sec_idx:sec_idx + 600]
        m = _TITLE_RE.search(after)
        if m:
            return m.group(1).strip().strip('*').strip()
    # 폴백: 첫 줄
    first = text.strip().split('\n', 1)[0]
    return first[:80]


def _detect_category(brand: dict, product: dict | None) -> str:
    """카테고리 키 결정 — 상품 카테고리 → 브랜드 업종 → general."""
    valid = {k for k, _ in PRODUCT_CATEGORY_OPTIONS}
    pc = (product or {}).get('category', '') if product else ''
    pc_norm = _normalize_category(pc)
    if pc_norm in valid:
        return pc_norm
    bi = (brand or {}).get('industry', '')
    bi_norm = _normalize_category(bi)
    if bi_norm in valid:
        return bi_norm
    return 'general'


_CATEGORY_ALIASES = {
    '식품': 'food', '먹거리': 'food', 'food': 'food',
    '이유식': 'baby_food', '영유아': 'baby_food', '영유아식품': 'baby_food', 'baby_food': 'baby_food',
    '건강기능식품': 'health_supplement', '건기식': 'health_supplement', 'health_supplement': 'health_supplement',
    '화장품': 'cosmetics', '뷰티': 'cosmetics', 'cosmetics': 'cosmetics',
    '의료기기': 'medical_device', 'medical_device': 'medical_device',
    '생활': 'lifestyle', '가전': 'lifestyle', 'lifestyle': 'lifestyle',
    '의류': 'fashion', '패션': 'fashion', 'fashion': 'fashion',
    '일반': 'general', 'general': 'general',
}


def _normalize_category(raw: str) -> str:
    if not raw:
        return ''
    return _CATEGORY_ALIASES.get(raw.strip().lower(), raw.strip().lower())


def _related_creation_payload(supabase, ref_id: str) -> dict | None:
    """series/variant 모드용 — 참조 글 발췌."""
    if not ref_id:
        return None
    try:
        r = supabase.table('creations').select(
            'id,output_data,input_data'
        ).eq('id', ref_id).limit(1).execute()
        if not r.data:
            return None
        row = r.data[0]
        text = (row.get('output_data') or {}).get('text') or ''
        title = _extract_title(row.get('output_data') or {})
        # 본문 발췌 (## 본문 ~ ## 다음 섹션 사이에서 처음 800자)
        body_idx = text.find('## 본문')
        if body_idx >= 0:
            tail = text[body_idx + len('## 본문'):]
            next_sec = tail.find('\n## ')
            body = tail[:next_sec] if next_sec > 0 else tail
            excerpt = body.strip()[:800]
        else:
            excerpt = text.strip()[:800]
        return {'id': row.get('id'), 'title': title, 'excerpt': excerpt}
    except Exception as e:
        logger.debug(f'[blog] related creation 조회 실패: {e}')
        return None


# ─────────────────────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────────────────────

@create_bp.route('/blog', methods=['GET'])
@login_required
def blog():
    supabase = current_app.supabase
    brands = get_accessible_brands(supabase)
    if not brands:
        flash('먼저 브랜드 프로필을 등록해 주세요.', 'warning')
        return redirect(url_for('main.onboarding'))
    default_brand = get_default_brand(supabase)
    products = _accessible_products(supabase, brand_id=default_brand['id'] if default_brand else None)
    recent = _recent_blog_creations(supabase, current_user.id,
                                    default_brand['id'] if default_brand else None)
    return render_template('create/blog.html',
                           brands=brands,
                           default_brand=default_brand,
                           products=products,
                           recent_blogs=recent,
                           length_costs=BLOG_LENGTH_COSTS,
                           angle_options=BLOG_ANGLE_OPTIONS,
                           relation_modes=RELATION_MODE_OPTIONS)


@create_bp.route('/blog/products', methods=['GET'])
@login_required
def blog_products():
    """브랜드 변경 시 상품/이력을 동적 갱신."""
    supabase = current_app.supabase
    brand_id = request.args.get('brand_id', '').strip()
    products = _accessible_products(supabase, brand_id=brand_id or None)
    recent = _recent_blog_creations(supabase, current_user.id, brand_id or None)
    return jsonify({
        'ok': True,
        'products': [{'id': p['id'], 'name': p['name'],
                      'category': p.get('category', ''),
                      'image_url': p.get('image_url') or ''}
                     for p in products],
        'recent_blogs': [{'id': r['id'], 'title': r['title'][:80],
                          'angle': r.get('angle', ''),
                          'created_at': r.get('created_at', '')[:10]}
                         for r in recent],
    })


@create_bp.route('/blog/generate', methods=['POST'])
@login_required
def blog_generate():
    supabase = current_app.supabase
    brand_id = request.form.get('brand_id', '').strip()
    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    product_id = request.form.get('product_id', '').strip()
    product = _get_product(supabase, product_id) if product_id else None

    length = (request.form.get('length') or '1000').strip()
    relation_mode = (request.form.get('relation_mode') or 'new').strip()
    relation_ref_id = (request.form.get('relation_ref_id') or '').strip() or None

    # 비용 계산 (분량 + 변형 할인)
    try:
        length_int = int(length)
    except ValueError:
        length_int = 1000
    cost = get_blog_cost(length_int, relation_mode)

    input_data = {
        'topic':         request.form.get('topic', '').strip(),
        'keyword':       request.form.get('keyword', '').strip(),
        'details':       request.form.get('details', '').strip(),
        'purpose':       request.form.get('purpose', '정보제공').strip(),
        'angle':         request.form.get('angle', 'information').strip(),
        'length':        str(length_int),
        'seo_keywords':  request.form.get('seo_keywords', '').strip(),
        'relation_mode': relation_mode,
    }
    if relation_ref_id:
        input_data['relation_ref_id'] = relation_ref_id
    if product_id:
        input_data['product_id'] = product_id

    if not input_data['topic']:
        return jsonify(ok=False, message='주제를 입력해 주세요.')

    # 카테고리 → 시스템 금지어 + 디스클레이머
    category = _detect_category(brand, product)
    merged_avoids = combine_avoid_words(brand, product, category)

    # 이력 (new 모드일 때만 회피 리스트 주입)
    recent = []
    related = None
    if relation_mode == 'new':
        recent = _recent_blog_creations(supabase, current_user.id, brand['id'], limit=30)
    elif relation_mode in ('series', 'variant') and relation_ref_id:
        related = _related_creation_payload(supabase, relation_ref_id)

    # 프롬프트 빌드
    from services.prompts.blog import build_prompt
    system, user, max_tokens = build_prompt(
        brand, input_data,
        product=product,
        category=category,
        merged_avoid_words=merged_avoids,
        recent_creations=recent,
        related_creation=related,
    )

    # 후처리: 디스클레이머 부착
    def _post(text: str) -> str:
        return append_disclaimer(text, category)

    extra_fields = {
        'product_id':      product_id or None,
        'topic':           input_data['topic'],
        'keyword':         input_data['keyword'],
        'angle':           input_data['angle'],
        'length_chars':    length_int,
        'relation_mode':   relation_mode,
        'relation_ref_id': relation_ref_id,
    }

    ledger_note = f'블로그 ({length_int:,}자' + (', 변형' if relation_mode == 'variant' else '') + ')'

    result = run_text_generation(
        'blog', brand, input_data, system, user,
        point_cost=cost,
        ledger_note=ledger_note,
        extra_creation_fields=extra_fields,
        post_process=_post,
        max_tokens=max_tokens,
    )
    if result.get('ok'):
        result['cost_charged'] = cost
        result['category'] = category
    return jsonify(result)


# ─────────────────────────────────────────────────────────────
# Step 2 — 소구포인트 3개 AI 생성
# ─────────────────────────────────────────────────────────────

@create_bp.route('/blog/angles', methods=['POST'])
@login_required
def blog_angles():
    """브랜드·상품·방향성을 받아 소구포인트 시안 3개를 생성."""
    supabase = current_app.supabase
    data = request.get_json(force=True) or {}

    brand_id   = (data.get('brand_id')   or '').strip()
    product_id = (data.get('product_id') or '').strip()
    direction  = (data.get('direction')  or '').strip()

    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    product = _get_product(supabase, product_id) if product_id else None

    # ── 브랜드·상품 컨텍스트 ──
    from services.claude_service import build_brand_context, generate_text
    brand_ctx = build_brand_context(brand, product)

    system_prompt = (
        '당신은 한국 온라인 커머스 전문 마케터입니다. '
        '브랜드와 상품 정보를 분석해 블로그 포스트에 적합한 소구포인트(핵심 메시지 방향) 시안을 제안합니다. '
        '결과는 반드시 JSON 배열만 출력하세요. 마크다운이나 설명 텍스트 없이 순수 JSON만 출력합니다.'
    )

    direction_line = f'\n- 작성자 방향성: {direction}' if direction else ''
    user_prompt = f"""다음 브랜드·상품 정보를 바탕으로 블로그 포스트 소구포인트 시안 3개를 JSON 배열로 생성하세요.

[브랜드·상품 정보]
{brand_ctx}{direction_line}

각 시안은 아래 필드를 가져야 합니다:
- id: "angle_1", "angle_2", "angle_3"
- title: 소구포인트 제목 (10자 이내, 핵심 키워드)
- hook: 독자 관심을 끄는 한 줄 문구 (30자 이내)
- target: 타겟 독자 설명 (20자 이내)
- tone: 글의 톤 (예: 정보형, 감성형, 경험담형, 비교분석형)
- approach: 이 방향으로 글을 쓸 때의 접근 전략 (2~3문장)
- key_points: 본문에 반드시 포함할 핵심 포인트 3개 (문자열 배열)

JSON 배열 형식 예시:
[
  {{
    "id": "angle_1",
    "title": "...",
    "hook": "...",
    "target": "...",
    "tone": "...",
    "approach": "...",
    "key_points": ["...", "...", "..."]
  }},
  ...
]

순수 JSON만 출력하세요."""

    try:
        raw = generate_text(system_prompt, user_prompt, max_tokens=1500)
        # JSON 파싱 — 마크다운 코드블록 제거 후 파싱
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        # 배열 부분만 추출
        arr_start = clean.find('[')
        arr_end   = clean.rfind(']') + 1
        if arr_start >= 0 and arr_end > arr_start:
            clean = clean[arr_start:arr_end]
        angles = json.loads(clean)
        if not isinstance(angles, list) or not angles:
            raise ValueError('angles 배열이 비어있음')
        return jsonify(ok=True, angles=angles[:3])
    except Exception as e:
        logger.error(f'[blog/angles] 소구포인트 생성 실패: {e}')
        return jsonify(ok=False, message=f'소구포인트 생성 중 오류가 발생했습니다: {e}')


# ─────────────────────────────────────────────────────────────
# Step 4 — 이미지 프롬프트 3개 AI 생성
# ─────────────────────────────────────────────────────────────

_STYLE_CONTEXT = {
    'realistic':    '실사 사진 스타일 — 프로페셔널 제품 사진, 자연광 또는 스튜디오 조명, DSLR 고해상도',
    'illustration': '한국 일러스트 스타일 — 부드러운 파스텔 색감, 따뜻하고 귀여운 디지털 아트',
    'webtoon':      '한국 웹툰 스타일 — 깔끔한 선화, 선명한 색상, 귀여운 캐릭터, 만화적 표현',
    'minimal':      '미니멀 플랫 디자인 — 단순한 기하학적 형태, 흰 배경, 군더더기 없는 현대적 레이아웃',
}


@create_bp.route('/blog/image-prompts', methods=['POST'])
@login_required
def blog_image_prompts():
    """선택된 소구포인트·이미지 스타일·블로그 글을 받아 이미지 프롬프트 3개를 생성."""
    supabase = current_app.supabase
    data = request.get_json(force=True) or {}

    brand_id   = (data.get('brand_id')   or '').strip()
    product_id = (data.get('product_id') or '').strip()
    style      = (data.get('style')      or 'realistic').strip()
    angle      = data.get('angle') or {}        # 선택된 소구포인트 객체
    content    = (data.get('content')    or '').strip()  # 생성된 블로그 글 (앞 500자)
    direction  = (data.get('direction')  or '').strip()

    brand = get_brand_by_id(supabase, brand_id) if brand_id else get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 프로필이 없습니다.')

    product = _get_product(supabase, product_id) if product_id else None

    from services.claude_service import build_brand_context, generate_text
    brand_ctx = build_brand_context(brand, product)

    style_desc = _STYLE_CONTEXT.get(style, _STYLE_CONTEXT['realistic'])
    angle_title = angle.get('title', '') if isinstance(angle, dict) else str(angle)
    angle_hook  = angle.get('hook', '')  if isinstance(angle, dict) else ''
    content_excerpt = content[:600] if content else ''

    system_prompt = (
        '당신은 AI 이미지 생성 전문 프롬프트 엔지니어입니다. '
        '블로그 포스트의 소구포인트와 이미지 스타일에 맞는 영문 이미지 프롬프트를 작성합니다. '
        '결과는 반드시 JSON 배열만 출력하세요. 마크다운이나 설명 텍스트 없이 순수 JSON만 출력합니다.'
    )

    user_prompt = f"""아래 정보를 바탕으로 블로그 포스트에 사용할 이미지 프롬프트 3개를 JSON 배열로 생성하세요.

[브랜드·상품 정보]
{brand_ctx}

[소구포인트]
- 제목: {angle_title}
- 핵심 문구: {angle_hook}
{f'- 방향성: {direction}' if direction else ''}

[이미지 스타일]
{style_desc}

[블로그 글 발췌 (참고용)]
{content_excerpt if content_excerpt else '(아직 생성 전)'}

이미지 3장의 역할:
1. 인트로 이미지 — 독자의 시선을 잡는 메인 비주얼
2. 본문 이미지 — 핵심 내용을 시각적으로 보완하는 이미지
3. 아웃트로 이미지 — 구매/행동 유도를 위한 마무리 비주얼

각 프롬프트는 아래 필드를 가져야 합니다:
- role: 이미지 역할 (인트로/본문/아웃트로 등 한국어)
- prompt: 영문 이미지 생성 프롬프트 (구체적이고 상세하게, 50~80 단어)
- aspect: 이미지 비율 ("16:9" 또는 "1:1" 또는 "4:3")
- style_note: 이 이미지에 특별히 강조할 스타일 요소 (한국어, 1문장)

JSON 배열 형식:
[
  {{
    "role": "...",
    "prompt": "...",
    "aspect": "...",
    "style_note": "..."
  }},
  ...
]

순수 JSON만 출력하세요."""

    try:
        raw = generate_text(system_prompt, user_prompt, max_tokens=1200)
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        arr_start = clean.find('[')
        arr_end   = clean.rfind(']') + 1
        if arr_start >= 0 and arr_end > arr_start:
            clean = clean[arr_start:arr_end]
        prompts = json.loads(clean)
        if not isinstance(prompts, list) or not prompts:
            raise ValueError('prompts 배열이 비어있음')
        return jsonify(ok=True, prompts=prompts[:3])
    except Exception as e:
        logger.error(f'[blog/image-prompts] 이미지 프롬프트 생성 실패: {e}')
        return jsonify(ok=False, message=f'이미지 프롬프트 생성 중 오류가 발생했습니다: {e}')
