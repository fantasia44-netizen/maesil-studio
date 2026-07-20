"""매실 인사이트 외부 API 연동.

라우트:
- GET  /integrations                  설정 페이지 (토큰 입력 + 현재 상태)
- POST /integrations/connect          토큰 검증(/me) + 저장
- POST /integrations/disconnect       연결 해제
- GET  /integrations/import           인사이트 상품 목록 (검색/페이지네이션)
- POST /integrations/import           선택된 상품들을 스튜디오 products 로 일괄 등록
"""
from __future__ import annotations

import logging

from flask import (
    Blueprint, current_app, flash, jsonify, redirect, render_template, request,
    url_for,
)
from flask_login import current_user, login_required

from services.maesil_insight_client import (
    MaesilInsightError, friendly_error_message,
)
from services.maesil_insight_connection import (
    disconnect as conn_disconnect,
    get_client_for_user,
    get_connection,
    mark_error,
    mark_used,
    verify_and_save,
)
from services.wordpress_client import (
    WordPressError, friendly_error_message as wp_friendly_error,
)
from services.wordpress_connection import (
    disconnect as wp_disconnect_conn,
    get_connection as wp_get_connection,
    mark_error as wp_mark_error,
    verify_and_save as wp_verify_and_save,
)
from services.wordpress_publish import (
    create_google_post, create_full_post, publish_existing_post,
)
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


def _collect_all_image_urls(detail: dict) -> list:
    """인사이트 상품 상세 응답에서 모든 이미지 URL을 중복 없이 수집.

    수집 대상 필드 (우선순위 순):
      image_url, thumbnail_url, images[], thumbnail_images[],
      detail_images[], extra_images[], gallery_images[], media[]
    """
    seen: set = set()
    result: list = []

    def _add(url):
        if url and isinstance(url, str) and url.startswith('http') and url not in seen:
            seen.add(url)
            result.append(url)

    # 단일 URL 필드
    for field in ('image_url', 'thumbnail_url', 'main_image_url', 'cover_image_url'):
        _add(detail.get(field))

    # 배열 필드
    for field in ('images', 'thumbnail_images', 'thumbnails',
                  'detail_images', 'extra_images', 'gallery_images',
                  'media', 'photo_urls', 'image_urls'):
        raw = detail.get(field)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    _add(item)
                elif isinstance(item, dict):
                    # {"url": "...", "type": "..."} 형태
                    _add(item.get('url') or item.get('src') or item.get('image_url'))

    return result


def _download_and_store_images(supabase, user_id: str, product_ref: str, urls: list) -> list:
    """외부 이미지 URL 목록 → Supabase Storage 업로드 → 공개 URL 목록 반환.
    실패한 이미지는 원본 URL로 폴백.
    """
    import uuid, requests as req_lib
    stored = []
    for idx, url in enumerate(urls[:30]):  # 최대 30장
        try:
            r = req_lib.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': url,
            })
            r.raise_for_status()
            mime = r.headers.get('Content-Type', 'image/jpeg').split(';')[0]
            ext = 'jpg' if 'jpeg' in mime else mime.split('/')[-1]
            path = f'{user_id}/products/insight_{product_ref[:8]}_{idx}_{uuid.uuid4().hex[:6]}.{ext}'
            supabase.storage.from_('creations').upload(
                path, r.content, {'content-type': mime}
            )
            public_url = supabase.storage.from_('creations').get_public_url(path)
            stored.append(public_url)
        except Exception as e:
            logger.warning(f'[Integrations] image download failed ({url[:60]}): {e}')
            stored.append(url)  # 실패 시 원본 URL 유지
    return stored

integrations_bp = Blueprint('integrations', __name__, url_prefix='/integrations')


# ─────────────────────────────────────────────────────────────
# 설정 페이지
# ─────────────────────────────────────────────────────────────

def _op_id():
    """현재 사용자의 operator_id (없으면 None)."""
    return getattr(current_user, 'operator_id', None)


def _can_manage() -> bool:
    """연결 설정(등록/해제) 권한.
    - 개인 사용자: 본인만
    - 팀 모드: operator_admin 만 (일반 팀원은 읽기 전용)
    """
    op_id = _op_id()
    if not op_id:
        return True
    return getattr(current_user, 'is_operator_admin', False)


@integrations_bp.route('/')
@login_required
def index():
    conn = get_connection(current_user.id, operator_id=_op_id())

    from blueprints.create._base import get_accessible_brands, get_default_brand
    sb = current_app.supabase
    brands = get_accessible_brands(sb) if sb else []
    selected_brand_id = request.args.get('wp_brand_id') or ''
    selected_brand = None
    if selected_brand_id:
        selected_brand = next((b for b in brands if b['id'] == selected_brand_id), None)
    if not selected_brand:
        selected_brand = get_default_brand(sb) if sb else None
    brand_wp_connection = (
        wp_get_connection(selected_brand['id'])
        if selected_brand else None
    )

    return render_template(
        'integrations/index.html',
        connection=conn,
        can_manage=_can_manage(),
        brands=brands,
        selected_brand=selected_brand,
        brand_wp_connection=brand_wp_connection,
    )


@integrations_bp.route('/connect', methods=['POST'])
@login_required
def connect():
    """토큰 입력 → /me 검증 → 저장.

    팀 모드: operator_admin 만 연결 가능 (팀원 공유).
    """
    if not _can_manage():
        flash('토큰 연결은 팀 관리자만 설정할 수 있습니다.', 'warning')
        return redirect(url_for('integrations.index'))

    token = (request.form.get('token') or '').strip()
    if not token:
        flash('토큰을 입력해주세요.', 'warning')
        return redirect(url_for('integrations.index'))

    op_id = _op_id()
    try:
        conn = verify_and_save(current_user.id, token, operator_id=op_id)
        flash(
            f'{conn.get("insight_operator_name") or "운영사"} 계정과 연결되었습니다.',
            'success',
        )
    except MaesilInsightError as e:
        logger.warning(f'[Integrations] connect 실패 user={current_user.id}: {e}')
        try:
            mark_error(current_user.id, str(e), operator_id=op_id)
        except Exception:
            pass
        flash(friendly_error_message(e), 'danger')
    except Exception as e:
        logger.error(f'[Integrations] connect 예외: {e}')
        flash('연결 중 오류가 발생했습니다.', 'danger')

    return redirect(url_for('integrations.index'))


@integrations_bp.route('/disconnect', methods=['POST'])
@login_required
def disconnect():
    """연결 해제. 팀 모드: operator_admin 만 가능."""
    if not _can_manage():
        flash('연결 해제는 팀 관리자만 할 수 있습니다.', 'warning')
        return redirect(url_for('integrations.index'))

    op_id = _op_id()
    try:
        conn_disconnect(current_user.id, operator_id=op_id)
        flash('연결이 해제되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[Integrations] disconnect 예외: {e}')
        flash('해제 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('integrations.index'))


# ─────────────────────────────────────────────────────────────
# 상품 가져오기
# ─────────────────────────────────────────────────────────────

def _require_client():
    """클라이언트 반환 또는 (None, redirect_response).

    팀 모드도 operator 단위 공유 연결 사용.
    """
    client = get_client_for_user(current_user.id, operator_id=_op_id())
    if not client:
        flash('먼저 매실 인사이트 토큰을 등록해주세요.', 'warning')
        return None, redirect(url_for('integrations.index'))
    return client, None


@integrations_bp.route('/import', methods=['GET'])
@login_required
def import_list():
    """인사이트 상품 목록 표시 — 사용자 선택용."""
    client, redir = _require_client()
    if redir is not None:
        return redir

    try:
        page     = max(1, int(request.args.get('page', 1)))
        per_page = min(100, max(1, int(request.args.get('per_page', 50))))
    except (TypeError, ValueError):
        page, per_page = 1, 50

    keyword   = (request.args.get('keyword')   or '').strip() or None
    category  = (request.args.get('category')  or '').strip() or None
    brand_id  = (request.args.get('brand_id')  or '').strip() or None

    products: list[dict] = []
    pagination: dict = {}
    error_msg: str | None = None

    try:
        result = client.list_products(
            page=page, per_page=per_page,
            keyword=keyword, category=category,
        )
        products = result.get('products') or []
        pagination = result.get('pagination') or {}
        if products:
            logger.info('[integrations] 상품 첫번째 필드: %s', list(products[0].keys()))
        mark_used(current_user.id, operator_id=_op_id())
    except MaesilInsightError as e:
        logger.warning(f'[Integrations] list_products 실패: {e}')
        mark_error(current_user.id, str(e), operator_id=_op_id())
        error_msg = friendly_error_message(e)
    except Exception as e:
        logger.error(f'[Integrations] list_products 예외: {e}')
        error_msg = '인사이트 상품 조회 중 오류가 발생했습니다.'

    # 이미 가져온 상품(source_ref) — 선택된 브랜드 기준으로 중복 체크
    # brand_id 지정 시 해당 브랜드에서만 체크, 없으면 전체 체크
    sb = current_app.supabase
    already: set[str] = set()
    try:
        if sb and products:
            refs = [p.get('seller_product_id') or '' for p in products]
            refs = [r for r in refs if r]
            if refs:
                q = (sb.table('products')
                     .select('source_ref')
                     .eq('user_id', str(current_user.id))
                     .eq('source', 'maesil_insight')
                     .in_('source_ref', refs))
                if brand_id:
                    q = q.eq('brand_id', brand_id)
                ex = q.execute()
                already = {r['source_ref'] for r in (ex.data or []) if r.get('source_ref')}
    except Exception:
        pass

    # 가져오기 시 자동 매핑할 브랜드
    from blueprints.create._base import get_accessible_brands
    brands = get_accessible_brands(sb) if sb else []

    return render_template(
        'integrations/import.html',
        products=products,
        pagination=pagination,
        keyword=keyword or '',
        category=category or '',
        already=already,
        brands=brands,
        error_msg=error_msg,
    )


@integrations_bp.route('/import', methods=['POST'])
@login_required
def import_apply():
    """선택된 상품들을 스튜디오 products 로 일괄 등록."""
    client, redir = _require_client()
    if redir is not None:
        return redir

    selected_ids = request.form.getlist('seller_product_id')
    brand_id = (request.form.get('brand_id') or '').strip() or None
    if not selected_ids:
        flash('가져올 상품을 선택해주세요.', 'warning')
        return redirect(url_for('integrations.import_list'))

    sb = current_app.supabase
    if not sb:
        flash('DB 연결이 없어 가져오기를 진행할 수 없습니다.', 'danger')
        return redirect(url_for('integrations.import_list'))

    # 이미 가져온 것 사전 조회 (중복 차단)
    existing: set[str] = set()
    try:
        ex = (sb.table('products')
              .select('source_ref')
              .eq('user_id', str(current_user.id))
              .eq('source', 'maesil_insight')
              .in_('source_ref', selected_ids)
              .execute())
        existing = {r['source_ref'] for r in (ex.data or []) if r.get('source_ref')}
    except Exception:
        pass

    inserted = 0
    skipped_dup = 0
    failed = 0
    last_err: str | None = None

    for sid in selected_ids:
        if not sid:
            continue
        if sid in existing:
            skipped_dup += 1
            continue

        # 상세 호출 (image_url, features 보강)
        try:
            detail = client.get_product(sid)
        except MaesilInsightError as e:
            logger.warning(f'[Integrations] get_product({sid}) 실패: {e}')
            failed += 1
            last_err = friendly_error_message(e)
            continue
        except Exception as e:
            logger.error(f'[Integrations] get_product({sid}) 예외: {e}')
            failed += 1
            continue

        # 이미지 목록 수집 → Supabase Storage 다운로드
        ext_images = _collect_all_image_urls(detail)
        logger.info(f'[Integrations] {sid} 이미지 {len(ext_images)}장 수집')

        images = _download_and_store_images(sb, str(current_user.id), sid, ext_images)

        row = {
            'user_id':     str(current_user.id),
            'brand_id':    brand_id,
            'name':        detail.get('display_name') or '',
            'category':    detail.get('category') or '',
            'price':       int(detail.get('sale_price') or 0) or None,
            'product_url': detail.get('product_url') or '',
            'description': detail.get('description_summary') or '',
            'features':    detail.get('features') or [],
            'images':      images,
            'is_active':   True,
            'source':      'maesil_insight',
            'source_ref':  sid,
            'created_at':  now_kst().isoformat(),
            'updated_at':  now_kst().isoformat(),
        }
        if getattr(current_user, 'operator_id', None):
            row['operator_id'] = current_user.operator_id

        if not row['name']:
            failed += 1
            continue

        try:
            sb.table('products').insert(row).execute()
            inserted += 1
        except Exception as e:
            # 진단을 위해 실제 메시지를 화면에 노출 — 마이그레이션 미적용 등 진짜 원인이
            # '상품 저장 중 오류'로 뭉개지지 않도록.
            err_str = str(e).strip()[:300] or e.__class__.__name__
            logger.warning(f'[Integrations] insert 실패 sid={sid}: {err_str}')
            failed += 1
            last_err = err_str

    mark_used(current_user.id, operator_id=_op_id())

    parts = [f'{inserted}건 가져왔습니다.']
    if skipped_dup:
        parts.append(f'{skipped_dup}건은 이미 가져와서 건너뛰었습니다.')
    if failed:
        parts.append(f'{failed}건은 실패했습니다' + (f' ({last_err})' if last_err else '') + '.')
    cat = 'success' if inserted and not failed else ('warning' if inserted else 'danger')
    flash(' '.join(parts), cat)

    return redirect(url_for('product.index'))


# ═════════════════════════════════════════════════════════════
# 워드프레스 연동 (자체 호스팅 · REST API + 애플리케이션 비밀번호)
# 브랜드 단위 연결만 존재(폴백 없음) — 경험담/일반 블로그 모두 공용.
# ═════════════════════════════════════════════════════════════

@integrations_bp.route('/wordpress/brand/<brand_id>/connect', methods=['POST'])
@login_required
def wordpress_brand_connect(brand_id):
    """브랜드 전용 워드프레스 연결. 팀 모드: operator_admin 만 가능."""
    if not _can_manage():
        flash('워드프레스 연결은 팀 관리자만 설정할 수 있습니다.', 'warning')
        return redirect(url_for('integrations.index', wp_brand_id=brand_id))

    from blueprints.create._base import get_brand_by_id
    sb = current_app.supabase
    brand = get_brand_by_id(sb, brand_id) if sb else None
    if not brand:
        flash('브랜드를 찾을 수 없습니다.', 'danger')
        return redirect(url_for('integrations.index'))

    site_url = (request.form.get('site_url') or '').strip()
    username = (request.form.get('wp_username') or '').strip()
    app_pw   = (request.form.get('app_password') or '').strip()
    if not site_url or not username or not app_pw:
        flash('사이트 주소, 아이디, 애플리케이션 비밀번호를 모두 입력해주세요.', 'warning')
        return redirect(url_for('integrations.index', wp_brand_id=brand_id))

    try:
        conn = wp_verify_and_save(
            current_user.id,
            site_url=site_url, username=username, app_password=app_pw,
            brand_id=brand_id, operator_id=_op_id(),
        )
        flash(
            f'"{brand["name"]}" 브랜드에 워드프레스 "{conn.get("wp_display_name") or username}" '
            f'계정이 연결되었습니다.',
            'success',
        )
    except WordPressError as e:
        logger.warning(f'[WP] brand connect 실패 brand={brand_id}: {e}')
        try:
            wp_mark_error(brand_id, str(e))
        except Exception:
            pass
        flash(wp_friendly_error(e), 'danger')
    except ValueError as e:
        flash(f'입력값을 확인해주세요. ({e})', 'warning')
    except Exception as e:
        logger.error(f'[WP] brand connect 예외: {e}', exc_info=True)
        flash('워드프레스 연결 중 오류가 발생했습니다.', 'danger')

    return redirect(url_for('integrations.index', wp_brand_id=brand_id))


@integrations_bp.route('/wordpress/brand/<brand_id>/disconnect', methods=['POST'])
@login_required
def wordpress_brand_disconnect(brand_id):
    """브랜드 전용 워드프레스 연결 해제. 팀 모드: operator_admin 만 가능."""
    if not _can_manage():
        flash('연결 해제는 팀 관리자만 할 수 있습니다.', 'warning')
        return redirect(url_for('integrations.index', wp_brand_id=brand_id))

    try:
        wp_disconnect_conn(brand_id)
        flash('브랜드 워드프레스 연결이 해제되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[WP] brand disconnect 예외: {e}')
        flash('해제 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('integrations.index', wp_brand_id=brand_id))


@integrations_bp.route('/wordpress/publish', methods=['POST'])
@login_required
def wordpress_publish():
    """경험담/블로그 구글판 → 워드프레스 글로 발행 (기본 초안). AJAX JSON.

    brand_id 는 필수 — 브랜드 전용 연결로만 발행한다(폴백 없음).
    실제 발행 로직은 services/wordpress_publish.py 공용 함수(Celery 자동 발행과 공유).
    """
    data = request.get_json(force=True) or {}
    brand_id = (data.get('brand_id') or '').strip()
    if not brand_id:
        return jsonify(ok=False, message='브랜드를 선택해주세요.'), 400

    result = create_google_post(
        current_app.supabase, brand_id,
        data.get('google_text') or '',
        status=data.get('status') or 'draft',
        title_override=(data.get('title') or '').strip() or None,
    )
    return jsonify(**result)


@integrations_bp.route('/wordpress/publish-full', methods=['POST'])
@login_required
def wordpress_publish_full():
    """완성본(글 + 본문 이미지 + 썸네일) 통째로 워드프레스 발행. AJAX JSON.

    본문 이미지는 각 URL을 다운로드해 WP 미디어에 업로드 후 본문에 삽입하고,
    썸네일은 대표 이미지(featured)로 설정한다. brand_id 필수.
    Request JSON:
      brand_id       str            (필수)
      google_text    str            구글(워드프레스)판 원문 (편집본)
      body_images    list[str]      본문에 넣을 이미지 URL 목록 (썸네일 제외, 순서 유지)
      thumbnail_url  str            대표 이미지용 썸네일 URL (선택)
      status         str            draft | publish | pending (기본 draft)
      title          str            제목 오버라이드 (선택)
    """
    data = request.get_json(force=True) or {}
    brand_id = (data.get('brand_id') or '').strip()
    if not brand_id:
        return jsonify(ok=False, message='브랜드를 선택해주세요.'), 400

    body_images = data.get('body_images')
    body_images = [u for u in body_images if isinstance(u, str) and u] \
        if isinstance(body_images, list) else []

    result = create_full_post(
        current_app.supabase, brand_id,
        data.get('google_text') or '',
        body_image_urls=body_images,
        thumbnail_url=(data.get('thumbnail_url') or '').strip() or None,
        status=data.get('status') or 'draft',
        title_override=(data.get('title') or '').strip() or None,
    )
    return jsonify(**result)


@integrations_bp.route('/wordpress/brand/<brand_id>/post/<post_id>/go-live', methods=['POST'])
@login_required
def wordpress_go_live(brand_id, post_id):
    """자동 저장된 초안을 실제로 발행(publish)으로 전환. AJAX JSON.

    create_google_post로 새 글을 또 만들지 않고, 같은 post_id의 상태만 바꾼다.
    """
    result = publish_existing_post(current_app.supabase, brand_id, post_id)
    return jsonify(**result)
