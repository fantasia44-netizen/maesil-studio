"""블로그 포스트 생성 — 5단계 위자드 (소구포인트→글→이미지→완성본)."""
import json
import logging
import re
import uuid as _uuid
import requests
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
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────

def _accessible_products(supabase, brand_id: str | None = None) -> list:
    """현재 사용자가 접근 가능한 전체 상품 (브랜드 필터 없음 — 글 작성 시 모든 상품 선택 가능)."""
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
        # 활성만
        try:
            q = q.eq('is_active', True)
        except Exception:
            pass
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
# Draft 헬퍼
# ─────────────────────────────────────────────────────────────

def _get_blog_drafts(supabase, limit: int = 5) -> list[dict]:
    """현재 사용자의 blog draft 목록 (최신순)."""
    try:
        uid = current_user.id
        oid = getattr(current_user, 'operator_id', None)
        q = (supabase.table('creations')
             .select('id, step_reached, input_data, output_data, step_data, created_at, updated_at')
             .eq('creation_type', 'blog')
             .eq('status', 'draft'))
        q = q.eq('operator_id', oid) if oid else q.eq('user_id', uid)
        rows = q.order('updated_at', desc=True).limit(limit).execute().data or []
        # 각 draft에 step 라벨 추가
        _STEP_LABELS = {1: '기본설정', 2: '소구포인트 선택', 3: '글 초안 완료', 4: '이미지 작업 중', 5: '완성'}
        for r in rows:
            r['step_label'] = _STEP_LABELS.get(r.get('step_reached') or 1, '')
            r['direction']  = ((r.get('input_data') or {}).get('direction') or '')[:50]
            r['date']       = (r.get('updated_at') or r.get('created_at') or '')[:10]
        return rows
    except Exception as e:
        logger.debug(f'[blog] get_drafts 실패: {e}')
        return []


# ─────────────────────────────────────────────────────────────
# Draft 라우트
# ─────────────────────────────────────────────────────────────

@create_bp.route('/blog/save-draft', methods=['POST'])
@login_required
def blog_save_draft():
    """스텝 완료 시 임시저장 (포인트 차감 없음)."""
    supabase = current_app.supabase
    data     = request.get_json(force=True) or {}
    draft_id = (data.get('draft_id') or '').strip() or None
    now_str  = now_kst().isoformat()

    row = {
        'user_id':       current_user.id,
        'creation_type': 'blog',
        'status':        'draft',
        'step_reached':  int(data.get('step_reached') or 1),
        'input_data':    data.get('input_data') or {},
        'step_data':     data.get('step_data')  or {},
        'output_data':   data.get('output_data') or {},
        'points_used':   0,
        'updated_at':    now_str,
    }
    if getattr(current_user, 'operator_id', None):
        row['operator_id'] = current_user.operator_id

    try:
        if draft_id:
            supabase.table('creations').update(row).eq(
                'id', draft_id).eq('user_id', current_user.id).execute()
        else:
            row['id']         = str(_uuid.uuid4())
            row['created_at'] = now_str
            supabase.table('creations').insert(row).execute()
            draft_id = row['id']
        return jsonify(ok=True, draft_id=draft_id)
    except Exception as e:
        logger.warning(f'[blog] save_draft error: {e}')
        return jsonify(ok=False, message=str(e))


@create_bp.route('/blog/draft/<draft_id>', methods=['GET'])
@login_required
def blog_get_draft(draft_id):
    """draft 또는 완료 작업 불러오기 (이어서/재작업 공통)."""
    try:
        r = current_app.supabase.table('creations').select('*').eq(
            'id', draft_id).eq('user_id', current_user.id).limit(1).execute()
        row = (r.data or [None])[0]
        if not row:
            return jsonify(ok=False, message='작업을 찾을 수 없습니다.')
        if row.get('status') not in ('draft', 'done'):
            return jsonify(ok=False, message='접근할 수 없는 작업입니다.')
        return jsonify(ok=True, draft=row)
    except Exception as e:
        return jsonify(ok=False, message=str(e))


@create_bp.route('/blog/draft/<draft_id>', methods=['DELETE'])
@login_required
def blog_delete_draft(draft_id):
    """draft 삭제."""
    try:
        current_app.supabase.table('creations').delete().eq(
            'id', draft_id).eq('user_id', current_user.id).eq('status', 'draft').execute()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, message=str(e))


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
    drafts = _get_blog_drafts(supabase)

    # 제품별 이미지 목록 맵 (JS에서 이미지 피커에 사용)
    products_images_map = {}
    for p in products:
        imgs = list(p.get('images') or [])
        if p.get('image_url') and p['image_url'] not in imgs:
            imgs.insert(0, p['image_url'])
        products_images_map[p['id']] = [u for u in imgs if u]

    # 브랜드별 워드프레스 연결 여부 ("네이버+구글 세트" 발행 버튼 노출 판단용)
    from services.wordpress_connection import is_connected as wp_is_connected
    brand_wp_connected = {b['id']: wp_is_connected(b['id']) for b in brands}

    return render_template('create/blog.html',
                           brands=brands,
                           default_brand=default_brand,
                           products=products,
                           products_images_map=products_images_map,
                           recent_blogs=recent,
                           blog_drafts=drafts,
                           length_costs=BLOG_LENGTH_COSTS,
                           angle_options=BLOG_ANGLE_OPTIONS,
                           relation_modes=RELATION_MODE_OPTIONS,
                           brand_wp_connected=brand_wp_connected)


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


@create_bp.route('/blog/recent-done', methods=['GET'])
@login_required
def blog_recent_done():
    """브랜드별 완성 블로그 최근 5개 (재작업 배너용)."""
    supabase = current_app.supabase
    brand_id = request.args.get('brand_id', '').strip() or None
    items = _recent_blog_creations(supabase, current_user.id, brand_id, limit=5)
    return jsonify(ok=True, items=[
        {'id': r['id'],
         'title': (r.get('title') or r.get('topic') or '제목 없음')[:80],
         'created_at': (r.get('created_at') or '')[:10]}
        for r in items
    ])


@create_bp.route('/blog/ref-preview', methods=['GET'])
@login_required
def blog_ref_preview():
    """이전 블로그 내용 미리보기 (시리즈/변형 모드용)."""
    supabase   = current_app.supabase
    ref_id     = request.args.get('id', '').strip()
    if not ref_id:
        return jsonify(ok=False, message='id 필요')
    try:
        res = supabase.table('creations').select('id,output_data,input_data,created_at') \
            .eq('id', ref_id).eq('user_id', current_user.id).limit(1).execute()
        row = (res.data or [None])[0]
        if not row:
            return jsonify(ok=False, message='이전 글을 찾을 수 없습니다.')
        title   = _extract_title(row.get('output_data') or {})
        text    = (row.get('output_data') or {}).get('text', '')
        excerpt = text[:400].strip() if text else ''
        return jsonify(ok=True, title=title, excerpt=excerpt)
    except Exception as e:
        return jsonify(ok=False, message=str(e))


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
    targets = (request.form.get('targets') or 'naver').strip().lower()
    if targets not in ('naver', 'both'):
        targets = 'naver'

    # 비용 계산 (분량 + 변형 할인, 네이버+구글 세트는 2배 — 출력량 2배에 대응)
    try:
        length_int = int(length)
    except ValueError:
        length_int = 1000
    cost = get_blog_cost(length_int, relation_mode)
    if targets == 'both':
        cost *= 2

    input_data = {
        'topic':         request.form.get('topic', '').strip(),
        'keyword':       request.form.get('keyword', '').strip(),
        'details':       request.form.get('details', '').strip(),
        'purpose':       request.form.get('purpose', '정보제공').strip(),
        'angle':         request.form.get('angle', 'information').strip(),
        'length':        str(length_int),
        'seo_keywords':  request.form.get('seo_keywords', '').strip(),
        'relation_mode': relation_mode,
        'targets':       targets,
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
        recent = _recent_blog_creations(supabase, current_user.id, brand['id'], limit=10)
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
        targets=targets,
    )

    extra_fields = {
        'product_id':      product_id or None,
        'topic':           input_data['topic'],
        'keyword':         input_data['keyword'],
        'angle':           input_data['angle'],
        'length_chars':    length_int,
        'relation_mode':   relation_mode,
        'relation_ref_id': relation_ref_id,
    }

    ledger_note = ('블로그 (' + f'{length_int:,}자'
                   + (', 변형' if relation_mode == 'variant' else '')
                   + (', 네이버+구글 세트' if targets == 'both' else '') + ')')

    if targets == 'both':
        # 출력이 2배로 늘어나 메인 서버를 오래 블로킹하므로 백그라운드 처리
        # (services/async_generation.py 공용 헬퍼 — experience_blog/thumbnail과 동일 패턴)
        from services.async_generation import submit_async_generation, AsyncSubmitError
        from services.regulatory import get_disclaimer
        from tasks.blog_text_task import generate_blog_both

        disclaimer = get_disclaimer(category)
        extra_row = {k: v for k, v in {**extra_fields, 'brand_id': brand['id']}.items()
                    if v is not None}
        try:
            creation_id = submit_async_generation(
                owner=current_user, creation_type='blog', cost=cost,
                input_data=input_data,
                extra_row=extra_row,
                note_override=ledger_note,
                task_delay_fn=generate_blog_both.delay,
                task_kwargs=dict(system_prompt=system, user_prompt=user,
                                 max_tokens=max_tokens, disclaimer=disclaimer,
                                 brand_id=brand['id']),
            )
        except AsyncSubmitError as e:
            return jsonify(ok=False, message=str(e))
        except Exception as e:
            logger.error(f'[blog_generate] both 제출 실패: {e}', exc_info=True)
            return jsonify(ok=False, message='블로그 생성을 시작할 수 없습니다.')

        return jsonify(ok=True, id=creation_id, async_mode=True,
                       cost=cost, category=category)

    # ── 네이버 단독(기존 동작, 무변경) — 동기 처리 ──
    def _post(text: str) -> str:
        return append_disclaimer(text, category)

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


@create_bp.route('/blog/text/status/<creation_id>', methods=['GET'])
@login_required
def blog_text_status(creation_id):
    """'네이버+구글 세트' 백그라운드 생성 완료 여부 폴링."""
    from services.async_generation import render_status_response
    supabase = current_app.supabase
    if not supabase:
        return jsonify(ok=False, status='error', message='DB 연결이 없습니다.')
    try:
        row = supabase.table('creations').select(
            'id, status, output_data, user_id').eq('id', creation_id).single().execute().data
    except Exception:
        return jsonify(ok=False, status='error', message='조회 실패')
    return render_status_response(
        row, current_user.id,
        done_fields={'text': 'text', 'google_text': 'google_text',
                    'wp_auto_publish': 'wp_auto_publish'},
    )


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

def _translate_prompts_to_english(prompts: list, indices: list) -> None:
    """한글이 포함된 이미지 prompt 필드를 영문 번역 (in-place).

    imagen_service의 _has_korean / _translate_prompt 재사용.
    FLUX 계열 이미지 모델은 한글을 이해하지 못해 이미지가 깨지므로
    서버 사이드에서 강제 영문 변환한다.
    """
    from services.imagen_service import _has_korean, _translate_prompt
    for i in indices:
        original = prompts[i].get('prompt', '')
        if _has_korean(original):
            translated = _translate_prompt(original)
            prompts[i]['prompt'] = translated
            logger.info('[blog/image-prompts] 슬롯%d 한글→영문 번역: %.60s…', i, translated)


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
        'You are an expert AI image generation prompt engineer for Korean blog posts. '
        '━━ ABSOLUTE RULES — violating any rule causes severe image failure ━━ '
        ''
        'RULE 1 — English only: The "prompt" field MUST be English ONLY. '
        'NEVER write Korean/Japanese/Chinese characters anywhere in the prompt field. '
        ''
        'RULE 2 — No screen content: NEVER describe what is displayed ON any screen/device. '
        '  ❌ BAD: "smartphone showing sales graph" → causes corrupted text in image '
        '  ✅ GOOD: "holding a smartphone, smiling confidently" '
        '  ❌ BAD: "laptop displaying product listing" '
        '  ✅ GOOD: "working at a laptop, focused and engaged" '
        ''
        'RULE 3 — Person first: When the scene involves a person, START the prompt with the person. '
        '  Keep it to 3-4 core elements max: [person] + [action/emotion] + [setting] + [lighting]. '
        '  Too many elements cause FLUX to drop the person and generate a flat-lay instead. '
        '  ❌ BAD: long list of 8+ descriptors → FLUX generates objects, no person '
        '  ✅ GOOD: "Korean woman in her 30s, smiling, holding smartphone, bright home office, natural light" '
        ''
        'RULE 4 — No text on ANY surface or object: '
        'NEVER describe sticky notes, bulletin boards, whiteboards, posters, or signage with text. '
        'NEVER describe product boxes/packages without adding "plain" or "blank" before them. '
        '  ❌ BAD: "holding a product box" → FLUX prints CJK brand text on the box '
        '  ✅ GOOD: "holding a plain white box", "holding a small plain package" '
        '  ❌ BAD: "shelves with product boxes" '
        '  ✅ GOOD: "shelves with plain boxes and items" '
        'These cause FLUX to render garbled CJK/Chinese/Korean characters. '
        ''
        'You MAY use Korean only in the "role" and "style_note" fields. '
        'Output ONLY a pure JSON array — no markdown, no explanation.'
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
- prompt: [ENGLISH ONLY] AI image generation prompt in English, 50-80 words. ⚠ NEVER use Korean/Japanese/Chinese here — it corrupts the image.
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

    # 이미지 수에 비례한 동적 max_tokens (슬롯당 ~260 토큰 + 기본 오버헤드)
    img_max_tokens = max(1800, ai_count * 260 + 600)

    try:
        raw = generate_text(system_prompt, user_prompt, max_tokens=img_max_tokens,
                            model='claude-sonnet-4-6')
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
        arr_start = clean.find('[')
        arr_end   = clean.rfind(']') + 1
        if arr_start >= 0 and arr_end > arr_start:
            clean = clean[arr_start:arr_end]
        prompts = json.loads(clean)
        if not isinstance(prompts, list) or not prompts:
            raise ValueError('prompts 배열이 비어있음')

        # ── 한글 감지 → Haiku로 영문 변환 ─────────────────────────
        # FLUX 계열 이미지 모델은 한글을 처리하지 못해 이미지가 깨짐
        from services.imagen_service import _has_korean
        ko_indices = [i for i, p in enumerate(prompts)
                      if _has_korean(p.get('prompt', ''))]
        if ko_indices:
            logger.warning('[blog/image-prompts] 한글 감지 슬롯: %s → 영문 번역 시작', ko_indices)
            _translate_prompts_to_english(prompts, ko_indices)

        return jsonify(ok=True, prompts=prompts[:ai_count])
    except Exception as e:
        logger.error(f'[blog/image-prompts] 이미지 프롬프트 생성 실패: {e}')
        return jsonify(ok=False, message=f'이미지 프롬프트 생성 중 오류가 발생했습니다: {e}')


# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# Step 4-b — 블로그 썸네일 카드 생성
# ─────────────────────────────────────────────────────────────

@create_bp.route('/blog/thumbnail', methods=['POST'])
@login_required
def blog_thumbnail():
    """블로그 썸네일 카드 생성 (FLUX 배경 + PIL 텍스트 합성 또는 PIL 단독).

    Request JSON:
      line1        str   메인 텍스트 (흰색 대형)
      line2        str   서브 텍스트 (강조색 대형, 선택)
      brand_name   str   @워터마크용 브랜드명
      accent_color str   서브 텍스트 강조색 (기본 #FFD700)
      use_flux     bool  True → FLUX 배경 생성 (50P), False → PIL 그라데이션 (0P)
      bg_topic     str   FLUX 배경 프롬프트 힌트 (한글 가능, 자동 번역)
    """
    import base64 as _b64
    data        = request.get_json(force=True) or {}
    line1        = (data.get('line1') or '').strip()[:18]
    line2        = (data.get('line2') or '').strip()[:18]
    brand_name   = (data.get('brand_name') or '').strip()[:24]
    accent_color = (data.get('accent_color') or '#FFD700').strip()
    use_flux        = bool(data.get('use_flux', True))
    use_quotes      = bool(data.get('use_quotes', True))
    bg_topic        = (data.get('bg_topic') or '').strip()[:120]
    blog_text       = (data.get('blog_text') or '').strip()[:800]
    image_prompts   = (data.get('image_prompts') or '').strip()[:600]
    text_y_pct      = max(10, min(90, int(data.get('text_y_pct',  55))))
    font_size_pct   = max(50, min(150, int(data.get('font_size_pct', 115))))
    overlay_darkness = max(0, min(100, int(data.get('overlay_darkness', 78))))
    text_align      = (data.get('text_align') or 'center').strip()
    if text_align not in ('center', 'left', 'right'):
        text_align = 'center'
    line1_color     = (data.get('line1_color') or '#FFFFFF').strip()
    letter_spacing  = max(-10, min(30, int(data.get('letter_spacing', 0))))
    text_bg_color   = (data.get('text_bg_color') or '').strip()
    text_bg_opacity = max(0, min(100, int(data.get('text_bg_opacity', 60))))
    # 직접 업로드한 배경 이미지 (base64 data URL)
    bg_upload_data  = (data.get('bg_upload_data') or '').strip()
    # 기존 FLUX 배경 재사용 (글자만 수정 시 100P 절약 + 즉시 합성)
    existing_bg_url = (data.get('existing_bg_url') or '').strip()
    # 신뢰 가능한 호스트(우리가 생성한 URL)만 허용
    if existing_bg_url and not any(h in existing_bg_url for h in (
            'fal.media', 'supabase.co', 'fal.run')):
        existing_bg_url = ''

    if not line1:
        return jsonify(ok=False, message='메인 텍스트를 입력해 주세요.')

    # existing_bg_url이 있으면 FLUX 호출 안 함 → 무료 (텍스트만 재합성)
    will_generate_flux = use_flux and not existing_bg_url

    # ── 무료 경로: 기존 배경 재사용 / 직접 업로드 / PIL 그라데이션 — 빠르므로 동기 처리 ──
    if not will_generate_flux:
        bg_url = bg_upload_data or existing_bg_url or None
        if bg_upload_data:
            logger.info('[thumbnail] 업로드 배경 사용 (base64)')
        elif existing_bg_url:
            logger.info(f'[thumbnail] 기존 배경 재사용: {bg_url[:80]}')

        from services.imagen_service import generate_blog_thumbnail, upload_to_supabase
        img_bytes = generate_blog_thumbnail(
            line1=line1, line2=line2, background_url=bg_url, brand_name=brand_name,
            accent_color=accent_color, line1_color=line1_color, use_quotes=use_quotes,
            text_y_pct=text_y_pct, font_size_pct=font_size_pct,
            overlay_darkness=overlay_darkness, text_align=text_align,
            letter_spacing=letter_spacing, text_bg_color=text_bg_color,
            text_bg_opacity=text_bg_opacity,
        )
        b64 = f"data:image/png;base64,{_b64.b64encode(img_bytes).decode()}"
        try:
            import time as _time
            public_url = upload_to_supabase(b64, current_user.id,
                                            f'blog_thumbnail_{int(_time.time())}.png')
        except Exception:
            public_url = b64   # 스토리지 실패 시 base64 직접
        return jsonify(ok=True, url=public_url, cost=0, bg_url=bg_url or '', async_mode=False)

    # ── 유료 경로: FLUX 신규 배경 생성 — 워커 제출 ───────────────
    # brand_id 자동 결정 (creations NOT NULL 제약 회피 — 운영자 환경 호환)
    _brand_id_for_thumb = (data.get('brand_id') or '').strip()
    if not _brand_id_for_thumb:
        try:
            _default = get_default_brand(current_app.supabase)
            _brand_id_for_thumb = (_default or {}).get('id') or None
        except Exception:
            _brand_id_for_thumb = None

    from services.async_generation import submit_async_generation, AsyncSubmitError
    from tasks.blog_thumbnail_task import classic_thumbnail as classic_task
    try:
        creation_id = submit_async_generation(
            owner=current_user, creation_type='blog_thumbnail', cost=100,
            input_data={'line1': line1, 'line2': line2,
                       'accent': accent_color, 'bg_topic': bg_topic},
            extra_row={'brand_id': _brand_id_for_thumb} if _brand_id_for_thumb else None,
            task_delay_fn=classic_task.delay,
            task_kwargs=dict(
                line1=line1, line2=line2, brand_name=brand_name, accent_color=accent_color,
                use_quotes=use_quotes, bg_topic=bg_topic, blog_text=blog_text,
                image_prompts=image_prompts, text_y_pct=text_y_pct, font_size_pct=font_size_pct,
                overlay_darkness=overlay_darkness, text_align=text_align,
                line1_color=line1_color, letter_spacing=letter_spacing,
                text_bg_color=text_bg_color, text_bg_opacity=text_bg_opacity,
            ),
        )
    except AsyncSubmitError as e:
        return jsonify(ok=False, message=str(e))
    except Exception as e:
        logger.error(f'[blog/thumbnail] 제출 실패: {e}', exc_info=True)
        return jsonify(ok=False, message=f'썸네일 생성을 시작할 수 없습니다. ({str(e)[:120]})')

    logger.info(f'[blog/thumbnail] 제출 완료 cid={creation_id[:8]} '
                f'uid={current_user.id[:8]} brand={(_brand_id_for_thumb or "")[:8]}')
    return jsonify(ok=True, id=creation_id, async_mode=True, cost=100)


@create_bp.route('/blog/thumbnail/status/<creation_id>', methods=['GET'])
@login_required
def blog_thumbnail_status(creation_id):
    from services.async_generation import render_status_response
    supabase = current_app.supabase
    if not supabase:
        return jsonify(ok=False, status='error', message='DB 연결이 없습니다.')
    try:
        row = supabase.table('creations').select(
            'id, status, output_data, user_id').eq('id', creation_id).single().execute().data
    except Exception:
        return jsonify(ok=False, status='error', message='조회 실패')
    return render_status_response(row, current_user.id, done_fields={'url': 'url', 'bg_url': 'bg_url'})


# (디자인 카드형 썸네일 모드 제거됨 — 클래식/AI 씬 투트랙으로 통합.
#  render_thumbnail 은 AI 씬 텍스트 합성에서 계속 사용.)


# ─────────────────────────────────────────────────────────────
# 캐릭터 AI 정밀 누끼 (fal birefnet) — 무료 PIL 누끼로 부족할 때
# ─────────────────────────────────────────────────────────────
_CUTOUT_COST    = 30
_TRANSFORM_COST = 100


def _save_thumb_creation(url: str, style: str, cost: int, meta: dict) -> None:
    """썸네일 생성물을 creations에 기록 — 생성 이력 노출·재사용용. 실패해도 응답엔 영향 없음."""
    if not current_app.supabase or not url:
        return
    try:
        _brand_id = None
        try:
            _d = get_default_brand(current_app.supabase)
            _brand_id = (_d or {}).get('id') or None
        except Exception:
            _brand_id = None
        now_s = now_kst().isoformat()
        row = {
            'id': str(_uuid.uuid4()), 'user_id': current_user.id,
            'creation_type': 'blog_thumbnail',
            'input_data': dict(meta or {}, style=style),
            'output_data': {'url': url, 'image_url': url, 'style': style},
            'points_used': cost, 'status': 'done',
            'created_at': now_s, 'updated_at': now_s,
        }
        if _brand_id:
            row['brand_id'] = _brand_id
        if getattr(current_user, 'operator_id', None):
            row['operator_id'] = current_user.operator_id
        try:
            current_app.supabase.table('creations').insert(row).execute()
        except Exception as ins_e:
            logger.warning(f'[thumb creation] insert 1차 실패 → operator_id 빼고 재시도: {ins_e}')
            row.pop('operator_id', None)
            current_app.supabase.table('creations').insert(row).execute()
    except Exception as e:
        logger.warning(f'[thumb creation] 이력 기록 실패(무시): {e}')


@create_bp.route('/blog/thumbnail/cutout', methods=['POST'])
@login_required
def blog_thumbnail_cutout():
    """캐릭터 이미지 AI 정밀 누끼 — 워커 제출, 완료는 status 폴링."""
    data = request.get_json(force=True) or {}
    char_data = (data.get('character_data') or '').strip()
    if not char_data.startswith('data:image/'):
        return jsonify(ok=False, message='캐릭터 이미지를 먼저 업로드해 주세요.')

    from services.async_generation import submit_async_generation, AsyncSubmitError
    from tasks.blog_thumbnail_task import cutout as cutout_task
    try:
        creation_id = submit_async_generation(
            owner=current_user, creation_type='blog_thumbnail', cost=_CUTOUT_COST,
            input_data={'action': 'cutout'},
            task_delay_fn=cutout_task.delay,
            task_kwargs=dict(character_data=char_data),
        )
    except AsyncSubmitError as e:
        return jsonify(ok=False, message=str(e))
    except Exception as e:
        logger.error(f'[blog/thumbnail/cutout] 제출 실패: {e}', exc_info=True)
        return jsonify(ok=False, message='AI 누끼 요청 중 오류가 발생했습니다.')

    return jsonify(ok=True, id=creation_id, async_mode=True, cost=_CUTOUT_COST)


@create_bp.route('/blog/thumbnail/cutout/status/<creation_id>', methods=['GET'])
@login_required
def blog_thumbnail_cutout_status(creation_id):
    """AI 누끼 결과 URL → data URL로 재조립해 반환 (프론트 계약 유지)."""
    supabase = current_app.supabase
    if not supabase:
        return jsonify(ok=False, status='error', message='DB 연결이 없습니다.')
    try:
        row = supabase.table('creations').select(
            'id, status, output_data, user_id').eq('id', creation_id).single().execute().data
    except Exception:
        return jsonify(ok=False, status='error', message='조회 실패')
    if not row or row.get('user_id') != current_user.id:
        return jsonify(ok=False, status='error', message='권한이 없거나 찾을 수 없습니다.')

    status = row.get('status', '')
    if status == 'done':
        result_url = (row.get('output_data') or {}).get('result_url', '')
        try:
            import base64 as _b64
            r = requests.get(result_url, timeout=30)
            r.raise_for_status()
            out = f"data:image/png;base64,{_b64.b64encode(r.content).decode()}"
        except Exception as e:
            logger.warning(f'[blog/thumbnail/cutout/status] 결과 fetch 실패: {e}')
            out = result_url
        return jsonify(ok=True, status='done', character_data=out)
    elif status == 'failed':
        od = row.get('output_data') or {}
        return jsonify(ok=False, status='failed',
                       message=(od.get('error') or 'AI 누끼 실패') + ' (포인트는 환불되었습니다)')
    else:
        return jsonify(ok=True, status='generating')


@create_bp.route('/blog/thumbnail/transform-character', methods=['POST'])
@login_required
def blog_thumbnail_transform_character():
    """캐릭터 이미지 변형(img2img) — 워커 제출, 완료는 status 폴링."""
    data = request.get_json(force=True) or {}
    char_data = (data.get('character_data') or '').strip()
    style     = (data.get('style') or '').strip()[:120]
    if not char_data.startswith('data:image/'):
        return jsonify(ok=False, message='캐릭터 이미지를 먼저 업로드해 주세요.')
    if not style:
        return jsonify(ok=False, message='원하는 변형 스타일을 입력해 주세요.')

    from services.async_generation import submit_async_generation, AsyncSubmitError
    from tasks.blog_thumbnail_task import transform_character as transform_task
    try:
        creation_id = submit_async_generation(
            owner=current_user, creation_type='blog_thumbnail', cost=_TRANSFORM_COST,
            input_data={'action': 'transform', 'style': style},
            task_delay_fn=transform_task.delay,
            task_kwargs=dict(character_data=char_data, style=style),
        )
    except AsyncSubmitError as e:
        return jsonify(ok=False, message=str(e))
    except Exception as e:
        logger.error(f'[blog/thumbnail/transform] 제출 실패: {e}', exc_info=True)
        return jsonify(ok=False, message='캐릭터 변형 요청 중 오류가 발생했습니다.')

    return jsonify(ok=True, id=creation_id, async_mode=True, cost=_TRANSFORM_COST)


@create_bp.route('/blog/thumbnail/transform-character/status/<creation_id>', methods=['GET'])
@login_required
def blog_thumbnail_transform_character_status(creation_id):
    """캐릭터 변형 결과 URL → data URL로 재조립해 반환 (프론트 계약 유지)."""
    supabase = current_app.supabase
    if not supabase:
        return jsonify(ok=False, status='error', message='DB 연결이 없습니다.')
    try:
        row = supabase.table('creations').select(
            'id, status, output_data, user_id').eq('id', creation_id).single().execute().data
    except Exception:
        return jsonify(ok=False, status='error', message='조회 실패')
    if not row or row.get('user_id') != current_user.id:
        return jsonify(ok=False, status='error', message='권한이 없거나 찾을 수 없습니다.')

    status = row.get('status', '')
    if status == 'done':
        result_url = (row.get('output_data') or {}).get('result_url', '')
        try:
            import base64 as _b64
            r = requests.get(result_url, timeout=30)
            r.raise_for_status()
            out = f"data:image/png;base64,{_b64.b64encode(r.content).decode()}"
        except Exception as e:
            logger.warning(f'[blog/thumbnail/transform/status] 결과 fetch 실패: {e}')
            out = result_url
        return jsonify(ok=True, status='done', character_data=out)
    elif status == 'failed':
        od = row.get('output_data') or {}
        return jsonify(ok=False, status='failed',
                       message=(od.get('error') or '캐릭터 변형 실패') + ' (포인트는 환불되었습니다)')
    else:
        return jsonify(ok=True, status='generating')


# ─────────────────────────────────────────────────────────────
# AI 씬 썸네일 — 브랜드 마스코트 레퍼런스로 상황 장면 생성(nano-banana)
#   + 상단에 선명한 PIL 한글 텍스트 합성 (하이브리드)
# ─────────────────────────────────────────────────────────────
_SCENE_COST = 200


def _mascot_owner_filter(q):
    """마스코트 조회를 operator/user 소유 범위로 제한."""
    oid = getattr(current_user, 'operator_id', None)
    if oid:
        return q.or_(f'operator_id.eq.{oid},user_id.eq.{current_user.id}')
    return q.eq('user_id', current_user.id)


def _get_registered_mascot_urls(brand_id=None, limit: int = 2) -> list:
    """등록된 브랜드 마스코트 URL 목록(최신순). brand_id를 주면 그 브랜드 것만."""
    if not current_app.supabase:
        return []
    try:
        q = (current_app.supabase.table('creations')
             .select('output_data, created_at, brand_id')
             .eq('creation_type', 'brand_mascot'))
        q = _mascot_owner_filter(q)
        if brand_id:
            q = q.eq('brand_id', brand_id)   # 브랜드별 스코프 (다른 브랜드 마스코트 딸려오지 않게)
        rows = q.order('created_at', desc=True).limit(limit).execute().data or []
        urls = [(r.get('output_data') or {}).get('mascot_url') for r in rows]
        return [u for u in urls if u]
    except Exception as e:
        logger.warning(f'[blog/thumbnail/mascot] 조회 실패: {e}')
        return []


@create_bp.route('/blog/thumbnail/mascot', methods=['GET'])
@login_required
def blog_thumbnail_mascot_get():
    """등록된 브랜드 마스코트 URL 목록 반환 (패널 진입 시 자동 로드용). brand_id로 스코프."""
    brand_id = (request.args.get('brand_id') or '').strip() or None
    return jsonify(ok=True, mascots=_get_registered_mascot_urls(brand_id))


@create_bp.route('/blog/thumbnail/mascot/save', methods=['POST'])
@login_required
def blog_thumbnail_mascot_save():
    """현재 캐릭터(투명 PNG data URL)를 브랜드 기본 마스코트로 등록. 무료."""
    data = request.get_json(force=True) or {}
    char_data = (data.get('character_data') or '').strip()
    if not char_data.startswith('data:image/'):
        return jsonify(ok=False, message='등록할 캐릭터 이미지가 없습니다.')

    # 현재 선택된 브랜드에 귀속 (없으면 기본 브랜드)
    brand_id = (data.get('brand_id') or '').strip() or None
    if not brand_id:
        try:
            brand_id = (get_default_brand(current_app.supabase) or {}).get('id') or None
        except Exception:
            brand_id = None

    _uid = str(getattr(current_user, 'id', '') or '')
    from services.imagen_service import upload_to_supabase
    try:
        url = upload_to_supabase(char_data, _uid, 'brand_mascot.png')
    except Exception as e:
        logger.error(f'[blog/thumbnail/mascot/save] 업로드 실패: {e}', exc_info=True)
        return jsonify(ok=False, message='마스코트 저장에 실패했습니다.')

    try:
        if current_app.supabase:
            now_s = now_kst().isoformat()
            row = {
                'id': str(_uuid.uuid4()), 'user_id': _uid,
                'creation_type': 'brand_mascot',
                'input_data': {}, 'output_data': {'mascot_url': url},
                'points_used': 0, 'status': 'done',
                'created_at': now_s, 'updated_at': now_s,
            }
            if brand_id:
                row['brand_id'] = brand_id
            if getattr(current_user, 'operator_id', None):
                row['operator_id'] = current_user.operator_id
            current_app.supabase.table('creations').insert(row).execute()
    except Exception as e:
        logger.warning(f'[blog/thumbnail/mascot/save] 기록 실패(무시): {e}')

    logger.info(f'[blog/thumbnail/mascot/save] 등록 완료 uid={_uid[:8]}')
    return jsonify(ok=True, url=url)


@create_bp.route('/blog/thumbnail/scene', methods=['POST'])
@login_required
def blog_thumbnail_scene():
    """AI 씬 썸네일 — 워커 제출, 완료는 status 폴링."""
    data = request.get_json(force=True) or {}
    headline = (data.get('line1') or '').strip()[:40]
    topic    = (data.get('topic') or data.get('line1') or '').strip()[:80]
    if not headline:
        return jsonify(ok=False, message='메인 텍스트(헤드라인)를 입력해 주세요.')

    sub   = (data.get('sub') or '').strip()[:40]
    badge = (data.get('badge') or '').strip()[:20]
    cta   = (data.get('cta') or '').strip()[:24]
    theme = (data.get('theme') or 'baby_blue').strip()
    title_style = 'plate' if (data.get('title_style') == 'plate') else 'banner'

    from services.imagen_service import SCENE_STYLES, SCENE_STYLE_DEFAULT
    scene_style = (data.get('scene_style') or '').strip()
    if scene_style not in SCENE_STYLES:
        scene_style = SCENE_STYLE_DEFAULT

    # 마스코트 레퍼런스: 이번 세션 업로드 우선, 없으면 (현재 브랜드에) 등록된 마스코트.
    #   없으면(캐릭터 브랜딩 없는 일반 업체) refs=[] → 소품·장면만 그리는 모드로 진행.
    #   인물 그림체 시안은 캐릭터를 쓰지 않으므로 조회 자체를 건너뛴다.
    if SCENE_STYLES[scene_style]['subject'] != 'mascot':
        refs = []
    else:
        brand_id = (data.get('brand_id') or '').strip() or None
        char_data = (data.get('character_data') or '').strip()
        if char_data.startswith('data:image/'):
            refs = [char_data]
        else:
            refs = _get_registered_mascot_urls(brand_id)

    from services.async_generation import submit_async_generation, AsyncSubmitError
    from tasks.blog_thumbnail_task import scene as scene_task
    try:
        creation_id = submit_async_generation(
            owner=current_user, creation_type='blog_thumbnail', cost=_SCENE_COST,
            input_data={'line1': headline, 'sub': sub, 'badge': badge, 'cta': cta,
                       'topic': topic, 'theme': theme, 'title_style': title_style,
                       'scene_style': scene_style},
            task_delay_fn=scene_task.delay,
            task_kwargs=dict(headline=headline, sub=sub, badge=badge, cta=cta,
                            theme=theme, title_style=title_style, topic=topic, refs=refs,
                            style=scene_style),
        )
    except AsyncSubmitError as e:
        return jsonify(ok=False, message=str(e))
    except Exception as e:
        logger.error(f'[blog/thumbnail/scene] 제출 실패: {e}', exc_info=True)
        return jsonify(ok=False, message='AI 씬 생성 요청 중 오류가 발생했습니다.')

    return jsonify(ok=True, id=creation_id, async_mode=True, cost=_SCENE_COST)


@create_bp.route('/blog/thumbnail/scene/status/<creation_id>', methods=['GET'])
@login_required
def blog_thumbnail_scene_status(creation_id):
    from services.async_generation import render_status_response
    supabase = current_app.supabase
    if not supabase:
        return jsonify(ok=False, status='error', message='DB 연결이 없습니다.')
    try:
        row = supabase.table('creations').select(
            'id, status, output_data, user_id').eq('id', creation_id).single().execute().data
    except Exception:
        return jsonify(ok=False, status='error', message='조회 실패')
    return render_status_response(row, current_user.id, done_fields={'url': 'url', 'style': 'style'})


# ─────────────────────────────────────────────────────────────
# 최근 생성한 썸네일 배경 갤러리 (재사용용)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/blog/thumbnail/recent-backgrounds', methods=['GET'])
@login_required
def blog_thumbnail_recent_backgrounds():
    """현재 사용자가 최근 FLUX로 생성한 썸네일 배경 URL 목록 (재사용용)."""
    try:
        uid = current_user.id
        oid = getattr(current_user, 'operator_id', None)
        # status 'done' + 호환을 위해 'pending' 행도 조회 (output_data에 bg_url 있는 것만 필터)
        q = (current_app.supabase.table('creations')
             .select('id, user_id, operator_id, input_data, output_data, created_at, status')
             .eq('creation_type', 'blog_thumbnail')
             .in_('status', ['done', 'pending']))
        # 운영자 모드: 이전 행은 user_id만 있을 수 있으므로 OR로 둘 다 조회
        if oid:
            q = q.or_(f'operator_id.eq.{oid},user_id.eq.{uid}')
        else:
            q = q.eq('user_id', uid)
        rows = q.order('created_at', desc=True).limit(30).execute().data or []
        logger.info(f'[blog/thumbnail/recent] uid={uid[:8]} oid={(oid or "")[:8]} rows={len(rows)}')

        out, seen = [], set()
        for r in rows:
            bg = ((r.get('output_data') or {}).get('bg_url') or '').strip()
            if not bg or bg in seen:
                continue
            seen.add(bg)
            out.append({
                'id':        r.get('id'),
                'bg_url':    bg,
                'thumbnail_url': (r.get('output_data') or {}).get('thumbnail_url') or '',
                'bg_topic':  ((r.get('input_data')  or {}).get('bg_topic')  or '')[:40],
                'date':      (r.get('created_at') or '')[:10],
            })
            if len(out) >= 8:
                break
        return jsonify(ok=True, backgrounds=out)
    except Exception as e:
        logger.warning(f'[blog/thumbnail/recent] 조회 실패: {e}')
        return jsonify(ok=True, backgrounds=[])


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
    # 단락 앞 200자 + 글자 수 — 내용 기반 의미 매칭을 위해 충분한 컨텍스트 전달
    para_list = '\n'.join(
        f'[단락{i}] ({len(p)}자) {p[:200]}{"…" if len(p) > 200 else ""}'
        for i, p in enumerate(paragraphs)
    )
    # 이미지 role + 영문 프롬프트 요약 + style_note 포함 — 의미적 매칭 근거 제공
    img_lines = []
    for i, img in enumerate(images):
        tag  = ' ← 제품 메인컷' if img.get('is_product') else ' — 라이프스타일 씬'
        role = img.get('role', '이미지')
        prom = (img.get('prompt') or '')[:80]
        note = img.get('style_note') or ''
        line = f'[이미지{i}] {role}{tag}'
        if prom:
            line += f'\n  장면: {prom}'
        if note:
            line += f'\n  분위기: {note}'
        img_lines.append(line)
    img_list = '\n'.join(img_lines)

    system = (
        '당신은 한국 블로그 편집자입니다. '
        '각 이미지의 장면/분위기와 단락 내용을 의미적으로 매칭하여 '
        '독자 몰입감이 가장 높아지는 위치에 이미지를 배치합니다. '
        '순수 JSON만 출력하세요 — 마크다운, 설명 텍스트 없이.'
    )
    user_prompt = f"""블로그에 단락 {n_para}개, 이미지 {n_img}개를 삽입합니다.
각 이미지를 어느 단락 뒤에 배치할지 결정하세요 (단락 인덱스는 0부터).

[단락 목록 — 각 단락의 핵심 내용]
{para_list}

[이미지 목록 — 각 이미지의 장면/분위기]
{img_list}

배치 규칙 (중요도 순):
1. 의미 매칭 최우선 — 이미지 장면/분위기가 단락 내용과 가장 자연스럽게 어울리는 위치 선택
2. 제품 메인컷(is_product)은 초반(단락 0 또는 1) 바로 뒤에 배치 — 독자 신뢰 확보
3. 라이프스타일 씬은 글 전체에 고르게 분산 (특정 구간 몰림 금지)
4. 이미지 두 장 연속 배치 금지 (단락 최소 1개 간격 필수)
5. 마지막 단락(인덱스 {n_para - 1}) 뒤 배치도 허용

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


# ─────────────────────────────────────────────────────────────
# Step 5 — 완성본 저장 (글 + 이미지 배치)
# ─────────────────────────────────────────────────────────────

@create_bp.route('/blog/save-final', methods=['POST'])
@login_required
def blog_save_final():
    """블로그 완성본(글 + 이미지 배치)을 creation 레코드에 저장.

    Request JSON:
      creation_id: str   — 블로그 텍스트 creation ID
      images: [{role, url, is_product}]
      placements: [{after_para_idx, image_idx}]
    """
    supabase    = current_app.supabase
    data        = request.get_json(force=True) or {}
    creation_id = (data.get('creation_id') or '').strip()
    images      = data.get('images', [])
    placements  = data.get('placements', [])

    if not creation_id:
        return jsonify(ok=False, message='creation_id 없음')

    try:
        # 기존 output_data 조회
        row = supabase.table('creations').select('output_data').eq(
            'id', creation_id
        ).eq('user_id', current_user.id).limit(1).execute()
        if not row.data:
            return jsonify(ok=False, message='creation을 찾을 수 없습니다.')

        existing = row.data[0].get('output_data') or {}
        existing['images']     = images
        existing['placements'] = placements
        existing['has_final']  = True

        supabase.table('creations').update({
            'output_data': existing,
        }).eq('id', creation_id).execute()

        return jsonify(ok=True)
    except Exception as e:
        logger.error(f'[blog/save-final] {e}')
        return jsonify(ok=False, message=str(e))
