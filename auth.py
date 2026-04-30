"""매실 스튜디오 - 인증 (로그인/가입/로그아웃/비밀번호 재설정)"""
import bcrypt
import logging
from datetime import datetime, timedelta
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, current_app, session
)
from flask_login import login_user, logout_user, login_required, current_user

from models import User
from services.validators import validate_email, validate_password
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)


def _hash_password(password: str) -> str:
    if isinstance(password, str):
        password = password.encode('utf-8')
    return bcrypt.hashpw(password, bcrypt.gensalt()).decode('utf-8')


def _verify_password(password: str, password_hash: str) -> bool:
    if isinstance(password, str):
        password = password.encode('utf-8')
    if isinstance(password_hash, str):
        password_hash = password_hash.encode('utf-8')
    return bcrypt.checkpw(password, password_hash)


def _check_ip_rate_limit(ip: str) -> bool:
    config = current_app.config
    from services.rate_limiter import get_rate_limiter
    limiter = get_rate_limiter()
    return limiter.is_rate_limited(
        f'ip:{ip}',
        config.get('IP_RATE_LIMIT_ATTEMPTS', 20),
        config.get('IP_RATE_LIMIT_WINDOW', 900),
    )


def _check_account_lock(user_row: dict) -> bool:
    locked_until = user_row.get('locked_until')
    if not locked_until:
        return False
    if isinstance(locked_until, str):
        locked_until = datetime.fromisoformat(locked_until.replace('Z', '+00:00'))
    from services.tz_utils import ensure_aware
    return ensure_aware(locked_until) > now_kst()


# ──────────────────────────────────────────
# 로그인
# ──────────────────────────────────────────
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'GET':
        return render_template('auth/login.html')

    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    client_ip = request.remote_addr

    if _check_ip_rate_limit(client_ip):
        flash('너무 많은 로그인 시도입니다. 잠시 후 다시 시도하세요.', 'danger')
        return render_template('auth/login.html'), 429

    if not email or not password:
        flash('이메일과 비밀번호를 입력하세요.', 'warning')
        return render_template('auth/login.html')

    supabase = current_app.supabase
    if not supabase:
        flash('DB 연결 실패. 잠시 후 다시 시도하세요.', 'danger')
        return render_template('auth/login.html')

    try:
        result = supabase.table('users').select('*').eq('email', email).execute()
        if not result.data:
            flash('이메일 또는 비밀번호가 올바르지 않습니다.', 'danger')
            return render_template('auth/login.html')

        row = result.data[0]

        if row.get('is_deleted'):
            flash('탈퇴한 계정입니다.', 'danger')
            return render_template('auth/login.html')

        if not row.get('is_active', True):
            flash('비활성화된 계정입니다. support@maesil.io로 문의하세요.', 'danger')
            return render_template('auth/login.html')

        if _check_account_lock(row):
            flash('계정이 잠겨있습니다. 잠시 후 다시 시도하세요.', 'danger')
            return render_template('auth/login.html')

        if not _verify_password(password, row.get('password_hash', '')):
            fail_count = row.get('failed_login_count', 0) + 1
            max_attempts = current_app.config.get('LOGIN_MAX_ATTEMPTS', 5)
            lockout_min = current_app.config.get('LOGIN_LOCKOUT_MINUTES', 15)
            locked_until = None
            if fail_count >= max_attempts:
                locked_until = (now_kst() + timedelta(minutes=lockout_min)).isoformat()
            supabase.table('users').update({
                'failed_login_count': fail_count,
                'locked_until': locked_until,
            }).eq('id', row['id']).execute()
            remaining = max(0, max_attempts - fail_count)
            if remaining > 0:
                flash(f'비밀번호가 틀렸습니다. ({remaining}회 남음)', 'danger')
            else:
                flash(f'계정이 {lockout_min}분간 잠겼습니다.', 'danger')
            return render_template('auth/login.html')

        # 로그인 성공
        supabase.table('users').update({
            'failed_login_count': 0,
            'locked_until': None,
            'last_login_at': now_kst().isoformat(),
        }).eq('id', row['id']).execute()

        # 구독 상태 조회
        try:
            sub = supabase.table('subscriptions').select(
                'status, current_period_end'
            ).eq('user_id', row['id']).order('created_at', desc=True).limit(1).execute()
            if sub.data:
                row['subscription_status'] = sub.data[0].get('status', 'trial')
                row['current_period_end'] = sub.data[0].get('current_period_end')
        except Exception:
            pass

        user = User(row)
        login_user(user, remember=False)
        session.permanent = True
        session['last_activity'] = now_kst().isoformat()

        next_url = request.args.get('next')
        if next_url and next_url.startswith('/'):
            return redirect(next_url)

        # 브랜드 프로필 없으면 온보딩으로
        try:
            bp = supabase.table('brand_profiles').select('id').eq(
                'user_id', row['id']
            ).limit(1).execute()
            if not bp.data:
                return redirect(url_for('main.onboarding'))
        except Exception:
            pass

        return redirect(url_for('main.dashboard'))

    except Exception as e:
        logger.error(f'[AUTH] login error: {e}')
        flash('로그인 중 오류가 발생했습니다.', 'danger')
        return render_template('auth/login.html')


# ──────────────────────────────────────────
# 회원가입
# ──────────────────────────────────────────
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'GET':
        return render_template('auth/register.html')

    client_ip = request.remote_addr
    from services.rate_limiter import get_rate_limiter
    if get_rate_limiter().is_rate_limited(f'register:{client_ip}', 10, 3600):
        flash('너무 많은 가입 시도입니다. 잠시 후 다시 시도하세요.', 'danger')
        return render_template('auth/register.html'), 429

    email = validate_email(request.form.get('email', ''))
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '')
    password_confirm = request.form.get('password_confirm', '')
    agree_terms = request.form.get('agree_terms')
    agree_privacy = request.form.get('agree_privacy')

    if not agree_terms or not agree_privacy:
        flash('서비스 이용약관 및 개인정보 처리방침에 모두 동의해야 합니다.', 'warning')
        return render_template('auth/register.html')

    if not email:
        flash('유효한 이메일 주소를 입력하세요.', 'warning')
        return render_template('auth/register.html')

    pw_error = validate_password(password)
    if pw_error:
        flash(pw_error, 'warning')
        return render_template('auth/register.html')

    if password != password_confirm:
        flash('비밀번호가 일치하지 않습니다.', 'warning')
        return render_template('auth/register.html')

    if not name:
        name = email.split('@')[0]

    supabase = current_app.supabase
    if not supabase:
        flash('DB 연결 실패', 'danger')
        return render_template('auth/register.html')

    try:
        dup = supabase.table('users').select('id').eq('email', email).execute()
        if dup.data:
            flash('이미 가입된 이메일입니다.', 'warning')
            return render_template('auth/register.html')

        user_result = supabase.table('users').insert({
            'email': email,
            'name': name,
            'password_hash': _hash_password(password),
            'plan_type': 'free',
            'is_active': True,
            'failed_login_count': 0,
            'created_at': now_kst().isoformat(),
            'updated_at': now_kst().isoformat(),
        }).execute()

        if not user_result.data:
            flash('가입 중 오류가 발생했습니다.', 'danger')
            return render_template('auth/register.html')

        user_id = user_result.data[0]['id']

        # trial 구독 생성
        try:
            supabase.table('subscriptions').insert({
                'user_id': user_id,
                'plan_type': 'free',
                'status': 'trial',
                'created_at': now_kst().isoformat(),
                'updated_at': now_kst().isoformat(),
            }).execute()
        except Exception:
            pass

        # 약관 동의 이력
        try:
            user_agent = request.headers.get('User-Agent', '')[:500]
            agreed_at = now_kst().isoformat()
            supabase.table('consent_logs').insert([
                {'user_id': user_id, 'email': email, 'consent_type': 'terms',
                 'terms_version': '2026-04-01', 'agreed_at': agreed_at,
                 'ip_address': client_ip, 'user_agent': user_agent},
                {'user_id': user_id, 'email': email, 'consent_type': 'privacy',
                 'terms_version': '2026-04-01', 'agreed_at': agreed_at,
                 'ip_address': client_ip, 'user_agent': user_agent},
            ]).execute()
        except Exception:
            pass

        flash('가입 완료! 로그인하세요.', 'success')
        return redirect(url_for('auth.login'))

    except Exception as e:
        logger.error(f'[AUTH] register error: {e}')
        flash('가입 중 오류가 발생했습니다.', 'danger')
        return render_template('auth/register.html')


# ──────────────────────────────────────────
# 로그아웃
# ──────────────────────────────────────────
@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('로그아웃되었습니다.', 'info')
    resp = redirect(url_for('landing.home'))
    resp.delete_cookie('session')
    return resp


# ──────────────────────────────────────────
# 비밀번호 찾기
# ──────────────────────────────────────────
@auth_bp.route('/find-account', methods=['GET', 'POST'])
def find_account():
    if request.method == 'GET':
        return render_template('auth/find_account.html')

    client_ip = request.remote_addr
    from services.rate_limiter import get_rate_limiter
    if get_rate_limiter().is_rate_limited(f'find:{client_ip}', 5, 3600):
        flash('너무 많은 시도입니다.', 'danger')
        return render_template('auth/find_account.html'), 429

    email = request.form.get('email', '').strip().lower()
    supabase = current_app.supabase

    if not email or not supabase:
        flash('이메일을 입력하세요.', 'warning')
        return render_template('auth/find_account.html')

    try:
        result = supabase.table('users').select('id').eq('email', email).execute()
        if result.data:
            import jwt as pyjwt, time
            user_id = result.data[0]['id']
            secret = current_app.config['SECRET_KEY']
            token = pyjwt.encode({
                'uid': str(user_id), 'email': email, 'typ': 'pw_reset',
                'iat': int(time.time()), 'exp': int(time.time()) + 3600,
            }, secret, algorithm='HS256')
            reset_url = url_for('auth.reset_password', token=token, _external=True)
            try:
                from services.email import send_password_reset_email
                send_password_reset_email(email, reset_url)
            except Exception:
                pass
    except Exception as e:
        logger.error(f'[AUTH] find_account error: {e}')

    flash('비밀번호 재설정 링크가 이메일로 발송되었습니다.', 'success')
    return render_template('auth/find_account.html')


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    token = request.args.get('token', '') or request.form.get('token', '')
    if not token:
        flash('유효하지 않은 링크입니다.', 'danger')
        return redirect(url_for('auth.login'))

    import jwt as pyjwt
    secret = current_app.config.get('SECRET_KEY', '')
    try:
        payload = pyjwt.decode(token, secret, algorithms=['HS256'])
        if payload.get('typ') != 'pw_reset':
            raise pyjwt.InvalidTokenError
    except pyjwt.ExpiredSignatureError:
        flash('링크가 만료되었습니다. 다시 요청하세요.', 'danger')
        return redirect(url_for('auth.find_account'))
    except pyjwt.InvalidTokenError:
        flash('유효하지 않은 링크입니다.', 'danger')
        return redirect(url_for('auth.login'))

    if request.method == 'GET':
        return render_template('auth/reset_password.html', token=token, email=payload.get('email', ''))

    new_pw = request.form.get('new_password', '')
    confirm_pw = request.form.get('confirm_password', '')
    if len(new_pw) < 8:
        flash('비밀번호는 8자 이상이어야 합니다.', 'warning')
        return render_template('auth/reset_password.html', token=token, email=payload.get('email', ''))
    if new_pw != confirm_pw:
        flash('비밀번호가 일치하지 않습니다.', 'warning')
        return render_template('auth/reset_password.html', token=token, email=payload.get('email', ''))

    try:
        current_app.supabase.table('users').update({
            'password_hash': _hash_password(new_pw),
            'failed_login_count': 0,
            'locked_until': None,
        }).eq('id', payload['uid']).execute()
        session.clear()
        flash('비밀번호가 재설정되었습니다. 새 비밀번호로 로그인하세요.', 'success')
    except Exception as e:
        logger.error(f'[AUTH] reset_password error: {e}')
        flash('처리 중 오류가 발생했습니다.', 'danger')

    return redirect(url_for('auth.login'))
