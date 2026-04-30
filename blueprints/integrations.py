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
    Blueprint, current_app, flash, redirect, render_template, request,
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
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)

integrations_bp = Blueprint('integrations', __name__, url_prefix='/integrations')


# ─────────────────────────────────────────────────────────────
# 설정 페이지
# ─────────────────────────────────────────────────────────────

@integrations_bp.route('/')
@login_required
def index():
    conn = get_connection(current_user.id)
    return render_template('integrations/index.html', connection=conn)


@integrations_bp.route('/connect', methods=['POST'])
@login_required
def connect():
    """토큰 입력 → /me 검증 → 저장."""
    token = (request.form.get('token') or '').strip()
    if not token:
        flash('토큰을 입력해주세요.', 'warning')
        return redirect(url_for('integrations.index'))

    try:
        conn = verify_and_save(current_user.id, token)
        flash(
            f'{conn.get("insight_operator_name") or "운영사"} 계정과 연결되었습니다.',
            'success',
        )
    except MaesilInsightError as e:
        logger.warning(f'[Integrations] connect 실패 user={current_user.id}: {e}')
        # 실패도 last_error 기록 (사용자가 이전 연결 보유 시 진단용)
        try:
            mark_error(current_user.id, str(e))
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
    """연결 해제."""
    try:
        conn_disconnect(current_user.id)
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

    연결이 없으면 설정 페이지로 리다이렉트.
    """
    client = get_client_for_user(current_user.id)
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

    keyword  = (request.args.get('keyword')  or '').strip() or None
    category = (request.args.get('category') or '').strip() or None

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
        mark_used(current_user.id)
    except MaesilInsightError as e:
        logger.warning(f'[Integrations] list_products 실패: {e}')
        mark_error(current_user.id, str(e))
        error_msg = friendly_error_message(e)
    except Exception as e:
        logger.error(f'[Integrations] list_products 예외: {e}')
        error_msg = '인사이트 상품 조회 중 오류가 발생했습니다.'

    # 이미 가져온 상품(source_ref) — 중복 표시용
    sb = current_app.supabase
    already: set[str] = set()
    try:
        if sb and products:
            refs = [p.get('seller_product_id') or '' for p in products]
            refs = [r for r in refs if r]
            if refs:
                ex = (sb.table('products')
                      .select('source_ref')
                      .eq('user_id', str(current_user.id))
                      .eq('source', 'maesil_insight')
                      .in_('source_ref', refs)
                      .execute())
                already = {r['source_ref'] for r in (ex.data or []) if r.get('source_ref')}
    except Exception:
        pass

    # 가져오기 시 자동 매핑할 브랜드 (기본 브랜드 우선)
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

        # 이미지 목록 수집 (image_url + images 배열)
        raw_images = detail.get('images') or []
        if isinstance(raw_images, list):
            images = [i for i in raw_images if isinstance(i, str) and i]
        else:
            images = []
        if detail.get('image_url') and detail['image_url'] not in images:
            images.insert(0, detail['image_url'])

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

    mark_used(current_user.id)

    parts = [f'{inserted}건 가져왔습니다.']
    if skipped_dup:
        parts.append(f'{skipped_dup}건은 이미 가져와서 건너뛰었습니다.')
    if failed:
        parts.append(f'{failed}건은 실패했습니다' + (f' ({last_err})' if last_err else '') + '.')
    cat = 'success' if inserted and not failed else ('warning' if inserted else 'danger')
    flash(' '.join(parts), cat)

    return redirect(url_for('product.index'))
