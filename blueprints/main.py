"""대시보드 / 이력 / 온보딩"""
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required, current_user
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)
main_bp = Blueprint('main', __name__)


@main_bp.before_request
@login_required
def require_auth():
    pass


def _scoped_creations_query(supabase, *, columns: str = '*'):
    """현재 사용자 풀(operator 또는 user) 의 creations 쿼리."""
    q = supabase.table('creations').select(columns)
    if current_user.operator_id:
        return q.eq('operator_id', current_user.operator_id)
    return q.is_('operator_id', 'null').eq('user_id', current_user.id)


def _get_brand_count(supabase) -> int:
    """operator OR logic 으로 접근 가능한 브랜드 수 반환.

    _scoped_creations_query 와 달리 brand_profiles 는 OR 매칭이 필요
    (operator_id 없이 user_id 만 있는 구형 브랜드도 포함).
    get_accessible_brands() 와 동일한 로직을 재사용.
    """
    from blueprints.create._base import get_accessible_brands
    try:
        brands = get_accessible_brands(supabase)
        return len(brands)
    except Exception as e:
        logger.debug(f'[DASHBOARD] brand count 실패: {e}')
        return 0


@main_bp.route('/dashboard')
@login_required
def dashboard():
    supabase = current_app.supabase
    balance = 0
    recent_creations = []
    brand_count = 0

    try:
        from services.point_service import get_balance
        balance = get_balance(current_user)

        recent = _scoped_creations_query(
            supabase,
            columns='id, creation_type, status, points_used, created_at, output_data, user_id',
        ).order('created_at', desc=True).limit(6).execute()
        recent_creations = recent.data or []

        brand_count = _get_brand_count(supabase)
    except Exception as e:
        logger.error(f'[DASHBOARD] error: {e}')

    return render_template('dashboard/index.html',
                           balance=balance,
                           recent_creations=recent_creations,
                           brand_count=brand_count,
                           is_team_mode=bool(current_user.operator_id))


@main_bp.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    if request.method == 'GET':
        return render_template('onboarding/index.html')

    supabase = current_app.supabase
    data = {
        'user_id': current_user.id,
        'name': request.form.get('name', '').strip(),
        'industry': request.form.get('industry', '').strip(),
        'target_customer': request.form.get('target_customer', '').strip(),
        'brand_tone': [t.strip() for t in request.form.get('brand_tone', '').split(',') if t.strip()],
        'keywords': [k.strip() for k in request.form.get('keywords', '').split(',') if k.strip()],
        'extra_context': request.form.get('extra_context', '').strip(),
        'is_default': True,
        'created_at': now_kst().isoformat(),
        'updated_at': now_kst().isoformat(),
    }

    if not data['name']:
        flash('브랜드명을 입력하세요.', 'warning')
        return render_template('onboarding/index.html')

    try:
        supabase.table('brand_profiles').insert(data).execute()
        flash('브랜드 프로필이 등록되었습니다!', 'success')
        return redirect(url_for('main.dashboard'))
    except Exception as e:
        logger.error(f'[ONBOARDING] error: {e}')
        flash('오류가 발생했습니다. 다시 시도해 주세요.', 'danger')
        return render_template('onboarding/index.html')


@main_bp.route('/history')
@login_required
def history():
    """생성 이력 — 유형별 그룹 뷰.

    뷰 모드:
      grouped (기본): 유형별 섹션으로 묶어서 표시.
      flat: 평면 시간순 (선택 시 한 유형으로 필터해서 봄).
    """
    supabase = current_app.supabase
    type_filter = request.args.get('type', '').strip()
    view_mode = request.args.get('view', 'grouped').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 20

    # 유형별 표시 우선순위 (대시보드/생성 메뉴 순서와 일치)
    # image.py  → img_preview / img_ideogram / img_card_news / bg_replace
    # logo.py   → logo
    # shorts.py → shorts_script / shorts_video
    TYPE_ORDER = [
        'blog', 'instagram', 'detail_page', 'thumbnail_text', 'ad_copy',
        'press_release', 'thumbnail_image', 'detail_image', 'card_news',
        'img_preview', 'img_ideogram', 'img_card_news', 'image_generation',
        'bg_replace', 'bg_remove_adv',
        'logo', 'shorts_script', 'shorts_video',
        'business_proposal', 'sponsorship_proposal', 'catalog', 'leaflet', 'flyer',
        'brand_package', 'product_launch',
    ]
    GROUP_LIMIT_PER_TYPE = 12

    creations: list = []
    grouped: list = []
    type_counts: dict = {}

    try:
        # 유형별 카운트 (전체 기간 — 운영자 풀)
        try:
            cnt = _scoped_creations_query(supabase, columns='creation_type').execute()
            for r in (cnt.data or []):
                k = r.get('creation_type') or ''
                if k:
                    type_counts[k] = type_counts.get(k, 0) + 1
        except Exception as e:
            logger.debug(f'[HISTORY] count 실패: {e}')

        if view_mode == 'flat' or type_filter:
            # 평면 모드 (유형 필터링 시 자동으로 평면 표시)
            offset = (page - 1) * per_page
            q = _scoped_creations_query(supabase)
            if type_filter:
                q = q.eq('creation_type', type_filter)
            result = (q.order('created_at', desc=True)
                       .range(offset, offset + per_page - 1).execute())
            creations = result.data or []
            view_mode = 'flat'
        else:
            # 그룹 모드 — 유형별로 최근 N건만
            for t in TYPE_ORDER:
                if t not in type_counts:
                    continue
                try:
                    rs = (_scoped_creations_query(supabase)
                          .eq('creation_type', t)
                          .order('created_at', desc=True)
                          .limit(GROUP_LIMIT_PER_TYPE)
                          .execute())
                    rows = rs.data or []
                except Exception as e:
                    logger.debug(f'[HISTORY] group({t}) 실패: {e}')
                    rows = []
                if rows:
                    grouped.append({
                        'type':     t,
                        'count':    type_counts.get(t, 0),
                        'rows':     rows,
                        'has_more': type_counts.get(t, 0) > GROUP_LIMIT_PER_TYPE,
                    })

    except Exception as e:
        logger.error(f'[HISTORY] error: {e}')

    from models import CREATION_LABELS
    return render_template('history/index.html',
                           creations=creations,
                           grouped=grouped,
                           type_counts=type_counts,
                           type_total=sum(type_counts.values()),
                           view_mode=view_mode,
                           page=page,
                           per_page=per_page,
                           type_filter=type_filter,
                           is_team_mode=bool(current_user.operator_id),
                           CREATION_LABELS=CREATION_LABELS)


@main_bp.route('/history/<creation_id>')
@login_required
def history_detail(creation_id):
    supabase = current_app.supabase
    try:
        # 팀 모드: operator 풀 전체에서 조회 (팀원도 관리자 생성물 열람 가능)
        # 개인 모드: user_id 로만 조회
        q = supabase.table('creations').select('*').eq('id', creation_id)
        if current_user.operator_id:
            q = q.eq('operator_id', current_user.operator_id)
        else:
            q = q.eq('user_id', current_user.id)
        result = q.execute()
        if not result.data:
            flash('생성물을 찾을 수 없습니다.', 'warning')
            return redirect(url_for('main.history'))
        creation = result.data[0]
    except Exception as e:
        logger.error(f'[HISTORY_DETAIL] error: {e}')
        flash('오류가 발생했습니다.', 'danger')
        return redirect(url_for('main.history'))

    from models import CREATION_LABELS
    return render_template('history/detail.html',
                           creation=creation,
                           CREATION_LABELS=CREATION_LABELS)


@main_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'GET':
        return render_template('main/settings.html')

    supabase = current_app.supabase
    name = request.form.get('name', '').strip()
    if not name:
        flash('이름을 입력하세요.', 'warning')
        return render_template('main/settings.html')

    try:
        supabase.table('users').update({
            'name': name,
            'updated_at': now_kst().isoformat(),
        }).eq('id', current_user.id).execute()
        flash('정보가 업데이트되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[SETTINGS] error: {e}')
        flash('오류가 발생했습니다.', 'danger')

    return redirect(url_for('main.settings'))


@main_bp.route('/settings/change-password', methods=['POST'])
@login_required
def change_password():
    import bcrypt
    current_pw = request.form.get('current_password', '')
    new_pw = request.form.get('new_password', '')
    confirm_pw = request.form.get('confirm_password', '')

    if new_pw != confirm_pw:
        flash('새 비밀번호가 일치하지 않습니다.', 'warning')
        return redirect(url_for('main.settings'))

    from services.validators import validate_password
    err = validate_password(new_pw)
    if err:
        flash(err, 'warning')
        return redirect(url_for('main.settings'))

    supabase = current_app.supabase
    try:
        row = supabase.table('users').select('password_hash').eq('id', current_user.id).execute()
        if not row.data:
            flash('사용자 정보를 찾을 수 없습니다.', 'danger')
            return redirect(url_for('main.settings'))

        pw_hash = row.data[0]['password_hash']
        if not bcrypt.checkpw(current_pw.encode(), pw_hash.encode()):
            flash('현재 비밀번호가 올바르지 않습니다.', 'danger')
            return redirect(url_for('main.settings'))

        new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
        supabase.table('users').update({
            'password_hash': new_hash,
            'updated_at': now_kst().isoformat(),
        }).eq('id', current_user.id).execute()
        flash('비밀번호가 변경되었습니다.', 'success')
    except Exception as e:
        logger.error(f'[CHANGE_PW] error: {e}')
        flash('오류가 발생했습니다.', 'danger')

    return redirect(url_for('main.settings'))
