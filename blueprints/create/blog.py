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

    # 제품별 이미지 목록 맵 (JS에서 이미지 피커에 사용)
    products_images_map = {}
    for p in products:
        imgs = list(p.get('images') or [])
        if p.get('image_url') and p['image_url'] not in imgs:
            imgs.insert(0, p['image_url'])
        products_images_map[p['id']] = [u for u in imgs if u]

    return render_template('create/blog.html',
                           brands=brands,
                           default_brand=default_brand,
                           products=products,
                           products_images_map=products_images_map,
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
    def _product_images(p):
        imgs = list(p.get('images') or [])
        if p.get('image_url') and p['image_url'] not in imgs:
            imgs.insert(0, p['image_url'])
        return [u for u in imgs if u]

    return jsonify({
        'ok': True,
        'products': [{'id': p['id'], 'name': p['name'],
                      'category': p.get('category', ''),
                      'image_url': p.get('image_url') or '',
                      'images': _product_images(p)}
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

    has_product_image = bool(data.get('has_product_image'))
    total_count = max(1, min(15, int(data.get('image_count') or 5)))
    # 제품 원본이 슬롯1을 차지하므로 AI 생성 수 = total - 1
    ai_count = (total_count - 1) if has_product_image else total_count
    ai_count = max(1, ai_count)

    if has_product_image:
        slots_desc = '\n'.join(
            f'{i+1}. 스토리/라이프스타일 이미지 {i+1} — 제품 없이 타겟 독자의 일상·감성·사용 맥락을 담은 장면'
            for i in range(ai_count)
        )
        image_plan = f"""이미지 {ai_count}장의 역할
(슬롯1은 실제 제품 원본 사진으로 이미 확정되어 있으니, AI 이미지에는 제품 패키지·제품 자체를 넣지 마세요)

{slots_desc}

중요: 모든 AI 이미지는 제품이 사용되는 상황, 감성, 라이프스타일만 표현합니다. 서로 중복되지 않게 각각 다른 씬/각도/분위기로 구성하세요."""
        slot_count = f'{ai_count}개'
    else:
        slots_desc = '\n'.join(
            f'{i+1}. {"인트로 — 시선을 잡는 메인 비주얼" if i==0 else "아웃트로 — 구매 유도 마무리 비주얼" if i==total_count-1 else f"본문 {i} — 핵심 내용 보완 라이프스타일 씬"}'
            for i in range(total_count)
        )
        image_plan = f"""이미지 {total_count}장의 역할:

{slots_desc}

서로 중복되지 않게 각각 다른 씬·각도·분위기로 구성하세요."""
        slot_count = f'{total_count}개'

    user_prompt = f"""아래 정보를 바탕으로 블로그 포스트에 사용할 이미지 프롬프트 {slot_count}를 JSON 배열로 생성하세요.

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

{image_plan}

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
        raw = generate_text(system_prompt, user_prompt, max_tokens=1200,
                            model='claude-haiku-4-5-20251001')
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        arr_start = clean.find('[')
        arr_end   = clean.rfind(']') + 1
        if arr_start >= 0 and arr_end > arr_start:
            clean = clean[arr_start:arr_end]
        prompts = json.loads(clean)
        if not isinstance(prompts, list) or not prompts:
            raise ValueError('prompts 배열이 비어있음')
        return jsonify(ok=True, prompts=prompts[:ai_count])
    except Exception as e:
        logger.error(f'[blog/image-prompts] 이미지 프롬프트 생성 실패: {e}')
        return jsonify(ok=False, message=f'이미지 프롬프트 생성 중 오류가 발생했습니다: {e}')


# ─────────────────────────────────────────────────────────────
# Step 5 — Haiku 이미지 배치 결정
# ─────────────────────────────────────────────────────────────

@create_bp.route('/blog/compose', methods=['POST'])
@login_required
def blog_compose():
    """블로그 글 + 이미지 목록 → Haiku가 이미지 삽입 위치 결정.

    Request JSON:
      blog_text: str               — 완성된 블로그 본문
      images: [{role, url, is_product}]  — 생성된 이미지 목록

    Response JSON:
      placements: [{after_para_idx: int, image_idx: int}]
      n_paragraphs: int
    """
    data      = request.get_json(force=True) or {}
    blog_text = (data.get('blog_text') or '').strip()
    images    = data.get('images', [])   # [{role, url, is_product}]

    if not blog_text:
        return jsonify(ok=False, message='블로그 텍스트가 없습니다.')
    if not images:
        return jsonify(ok=True, placements=[], n_paragraphs=0)

    # 이중 개행 기준으로 단락 분리 (빈 줄 제거)
    paragraphs = [p.strip() for p in blog_text.split('\n\n') if p.strip()]
    n_para = len(paragraphs)
    n_img  = len(images)

    if n_para == 0:
        return jsonify(ok=True, placements=[], n_paragraphs=0)

    # ── Haiku 프롬프트 ─────────────────────────────────────────
    para_list = '\n'.join(
        f'[단락{i}] {p[:100]}{"…" if len(p) > 100 else ""}'
        for i, p in enumerate(paragraphs)
    )
    img_list = '\n'.join(
        f'[이미지{i}] {img.get("role", "이미지")}'
        + (' ← 제품 메인컷' if img.get('is_product') else ' — 라이프스타일 씬')
        for i, img in enumerate(images)
    )

    system = (
        '당신은 한국 블로그 편집자입니다. '
        '독자 이탈을 방지하고 몰입감을 높이도록 이미지 삽입 위치를 결정합니다. '
        '순수 JSON만 출력하세요 — 마크다운, 설명 텍스트 없이.'
    )
    user_prompt = f"""블로그에 단락 {n_para}개, 이미지 {n_img}개를 삽입합니다.
각 이미지를 어느 단락 뒤에 배치할지 결정하세요 (단락 인덱스는 0부터).

[단락 목록]
{para_list}

[이미지 목록]
{img_list}

배치 규칙:
1. 제품 메인컷(is_product)은 초반(단락 0 또는 1) 바로 뒤에 배치 — 독자 신뢰 확보
2. 라이프스타일 씬은 글 전체에 고르게 분산
3. 이미지 두 장을 연속으로 붙이지 말 것 (단락 사이 최소 1개 단락 간격)
4. 마지막 단락(인덱스 {n_para - 1}) 뒤 배치도 가능
5. 내용과 가장 어울리는 단락 다음에 배치

응답 형식 (순수 JSON):
{{"placements": [{{"after_para_idx": 0, "image_idx": 0}}, ...]}}"""

    from services.claude_service import generate_text

    def _fallback_placements():
        """Haiku 실패 시 균등 분배."""
        step = max(1, n_para // (n_img + 1))
        return [
            {'after_para_idx': min((i + 1) * step - 1, n_para - 1), 'image_idx': i}
            for i in range(n_img)
        ]

    try:
        raw = generate_text(system, user_prompt, max_tokens=400,
                            model='claude-haiku-4-5-20251001')
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        obj_s = clean.find('{')
        obj_e = clean.rfind('}') + 1
        if obj_s >= 0 and obj_e > obj_s:
            clean = clean[obj_s:obj_e]
        result     = json.loads(clean)
        placements = result.get('placements', [])
        # 기본 검증
        for p in placements:
            if not isinstance(p.get('after_para_idx'), int):
                raise ValueError('after_para_idx가 정수가 아님')
        return jsonify(ok=True, placements=placements, n_paragraphs=n_para)
    except Exception as e:
        logger.warning(f'[blog/compose] Haiku 배치 실패, 폴백 사용: {e}')
        return jsonify(ok=True, placements=_fallback_placements(), n_paragraphs=n_para)
