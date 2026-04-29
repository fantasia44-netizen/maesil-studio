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


@main_bp.route('/dashboard')
@login_required
def dashboard():
    supabase = current_app.supabase
    balance = 0
    recent_creations = []
    brand_count = 0

    try:
        from services.point_service import get_balance
        balance = get_balance(current_user.id)

        recent = supabase.table('creations').select(
            'id, creation_type, status, points_used, created_at, output_data'
        ).eq('user_id', current_user.id).order(
            'created_at', desc=True
        ).limit(6).execute()
        recent_creations = recent.data or []

        bp = supabase.table('brand_profiles').select(
            'id', count='exact'
        ).eq('user_id', current_user.id).execute()
        brand_count = bp.count or 0
    except Exception as e:
        logger.error(f'[DASHBOARD] error: {e}')

    return render_template('dashboard/index.html',
                           balance=balance,
                           recent_creations=recent_creations,
                           brand_count=brand_count)


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
    supabase = current_app.supabase
    page = request.args.get('page', 1, type=int)
    type_filter = request.args.get('type', '')
    per_page = 20
    offset = (page - 1) * per_page

    try:
        q = supabase.table('creations').select('*').eq('user_id', current_user.id)
        if type_filter:
            q = q.eq('creation_type', type_filter)
        result = q.order('created_at', desc=True).range(offset, offset + per_page - 1).execute()
        creations = result.data or []
    except Exception as e:
        logger.error(f'[HISTORY] error: {e}')
        creations = []

    from models import CREATION_LABELS
    return render_template('history/index.html',
                           creations=creations,
                           page=page,
                           per_page=per_page,
                           type_filter=type_filter,
                           CREATION_LABELS=CREATION_LABELS)


@main_bp.route('/history/<creation_id>')
@login_required
def history_detail(creation_id):
    supabase = current_app.supabase
    try:
        result = supabase.table('creations').select('*').eq(
            'id', creation_id
        ).eq('user_id', current_user.id).execute()
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
