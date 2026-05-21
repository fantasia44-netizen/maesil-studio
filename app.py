"""매실 스튜디오 - Flask 앱 팩토리"""
import os
import logging
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, session, jsonify
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect

from config import DevelopmentConfig, ProductionConfig
from models import User
from services.tz_utils import now_kst


def create_app(config_class=None):
    app = Flask(__name__)

    if config_class is None:
        env = os.environ.get('FLASK_ENV', 'development')
        config_class = ProductionConfig if env == 'production' else DevelopmentConfig
    app.config.from_object(config_class)

    _init_supabase(app)
    _init_jinja_filters(app)
    _init_csrf(app)
    _init_login(app)
    _register_blueprints(app)
    _register_hooks(app)
    _init_redis(app)

    return app


# ──────────────────────────────────────────
def _init_supabase(app):
    url = app.config.get('SUPABASE_URL')
    key = app.config.get('SUPABASE_SERVICE_KEY') or app.config.get('SUPABASE_KEY')
    if not url or not key:
        app.logger.warning('[DB] Supabase 환경변수 미설정 — DB 비활성화')
        app.supabase = None
        return
    try:
        from supabase import create_client
        import httpx
        transport = httpx.HTTPTransport(http1=True, http2=False, retries=1)
        client = create_client(url, key)
        app.supabase = client
        app.logger.info('[DB] Supabase 연결 완료')
    except Exception as e:
        app.logger.error(f'[DB] Supabase 연결 실패: {e}')
        app.supabase = None


def _init_jinja_filters(app):
    from services.tz_utils import to_kst_str

    @app.template_filter('kst')
    def kst_filter(value, fmt='%Y-%m-%d %H:%M'):
        return to_kst_str(value, fmt)

    @app.template_filter('number')
    def number_filter(value):
        try:
            return f'{int(value):,}'
        except (ValueError, TypeError):
            return value

    @app.context_processor
    def inject_globals():
        from models import PLAN_FEATURES, POINT_COSTS, CREATION_LABELS
        ctx = {
            'app_name': app.config.get('APP_NAME', '매실 스튜디오'),
            'PLAN_FEATURES': PLAN_FEATURES,
            'POINT_COSTS': POINT_COSTS,
            'CREATION_LABELS': CREATION_LABELS,
            'nav_balance': None,
        }
        # 로그인 사용자: 네비게이션 포인트 잔액 주입 (캐시된 user_loader 이후라 DB 추가 조회 없음)
        if current_user.is_authenticated:
            try:
                from services.point_service import get_balance
                ctx['nav_balance'] = get_balance(current_user)
            except Exception:
                pass
        return ctx


def _init_csrf(app):
    csrf = CSRFProtect(app)

    @app.errorhandler(400)
    def csrf_error(e):
        if 'CSRF' in str(e):
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify(error='CSRF 토큰이 유효하지 않습니다.'), 400
            from flask import flash
            flash('요청이 만료되었습니다. 다시 시도하세요.', 'warning')
            return redirect(request.referrer or url_for('main.dashboard'))
        return e


def _init_login(app):
    login_manager = LoginManager(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = '로그인이 필요합니다.'
    login_manager.login_message_category = 'warning'

    _user_cache = {}
    CACHE_TTL = 300  # 5분
    # 캐시를 앱 객체에 노출 — view-as 종료 시 즉시 무효화 가능
    app.user_cache = _user_cache

    @login_manager.user_loader
    def load_user(user_id):
        import time
        cached = _user_cache.get(user_id)
        if cached and time.time() - cached['ts'] < CACHE_TTL:
            cached_user = cached['user']
            # view-as 모드가 아닌데 캐시 오염(id 불일치) 감지 → 캐시 무시하고 DB 재로드
            # (gunicorn 멀티워커 환경에서 다른 워커가 view-as 종료해도 이 워커 캐시는
            #  여전히 target_id 를 가지고 있을 수 있음)
            from flask import session as _fs
            if str(cached_user.id) != str(user_id) and not _fs.get('view_as_user_id'):
                _user_cache.pop(user_id, None)  # 오염된 캐시 제거
            else:
                return cached_user

        if not app.supabase:
            return None
        try:
            result = app.supabase.table('users').select('*').eq('id', user_id).execute()
            if not result.data:
                return None
            row = result.data[0]
            # 구독 상태
            sub = app.supabase.table('subscriptions').select(
                'status, current_period_end'
            ).eq('user_id', user_id).order('created_at', desc=True).limit(1).execute()
            if sub.data:
                row['subscription_status'] = sub.data[0].get('status', 'trial')
                row['current_period_end'] = sub.data[0].get('current_period_end')
            user = User(row)
            _user_cache[user_id] = {'user': user, 'ts': time.time()}
            return user
        except Exception as e:
            app.logger.error(f'[LOGIN] user_loader error: {e}')
            return None


def _register_blueprints(app):
    from auth import auth_bp
    app.register_blueprint(auth_bp)

    from blueprints.landing import landing_bp
    app.register_blueprint(landing_bp)

    from blueprints.main import main_bp
    app.register_blueprint(main_bp)

    from blueprints.brand import brand_bp
    app.register_blueprint(brand_bp, url_prefix='/brand')

    from blueprints.billing import billing_bp
    app.register_blueprint(billing_bp, url_prefix='/billing')

    from blueprints.product import product_bp
    app.register_blueprint(product_bp, url_prefix='/product')

    from blueprints.create import create_bp
    app.register_blueprint(create_bp, url_prefix='/create')

    from blueprints.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    from blueprints.integrations import integrations_bp
    app.register_blueprint(integrations_bp)

    from blueprints.team import team_bp
    app.register_blueprint(team_bp, url_prefix='/team')

    from blueprints.maeyo import maeyo_bp
    app.register_blueprint(maeyo_bp)


def _register_hooks(app):
    @app.before_request
    def check_inactivity():
        if not current_user.is_authenticated:
            return
        if request.endpoint in ('auth.login', 'auth.logout', 'static'):
            return
        timeout_min = app.config.get('SESSION_INACTIVITY_TIMEOUT', 120)
        last_str = session.get('last_activity')
        if last_str:
            try:
                last = datetime.fromisoformat(last_str)
                from services.tz_utils import ensure_aware
                last = ensure_aware(last)
                if now_kst() - last > timedelta(minutes=timeout_min):
                    from flask_login import logout_user
                    logout_user()
                    session.clear()
                    from flask import flash
                    flash('세션이 만료되었습니다. 다시 로그인하세요.', 'warning')
                    return redirect(url_for('auth.login'))
            except Exception:
                pass
        session['last_activity'] = now_kst().isoformat()

    @app.before_request
    def track_last_seen():
        """5분마다 users.last_seen_at 갱신 (정적 파일·로그인·로그아웃 제외)."""
        if not current_user.is_authenticated:
            return
        if request.endpoint in ('auth.login', 'auth.logout', 'static'):
            return
        last_tracked = session.get('last_seen_tracked')
        now = now_kst()
        should_update = True
        if last_tracked:
            try:
                from services.tz_utils import ensure_aware
                lt = ensure_aware(datetime.fromisoformat(last_tracked))
                if now - lt < timedelta(minutes=5):
                    should_update = False
            except Exception:
                pass
        if should_update and app.supabase:
            try:
                # view-as 모드일 때는 실제 어드민 ID 기준으로 갱신
                uid = session.get('view_as_admin_id') or current_user.get_id()
                app.supabase.table('users').update(
                    {'last_seen_at': now.isoformat()}
                ).eq('id', uid).execute()
                session['last_seen_tracked'] = now.isoformat()
            except Exception:
                pass

    @app.before_request
    def handle_view_as():
        """슈퍼어드민 — 유저로 보기 세션 처리.

        current_user.id 를 대상 유저 ID로 교체해
        모든 DB 쿼리(생성이력·포인트·브랜드 등)가 해당 유저 기준으로 동작.
        어드민 권한(is_superadmin) 은 site_role 을 바꾸지 않으므로 그대로 유지.
        """
        from flask import g
        view_as_uid = session.get('view_as_user_id')
        admin_id    = session.get('view_as_admin_id')
        if not view_as_uid or not admin_id:
            return
        if not current_user.is_authenticated:
            return

        # ── 어드민 ID 검증 ──────────────────────────────────────────
        # ※ current_user.get_id() 는 handle_view_as 가 이전 요청에서
        #   current_user.id 를 대상 유저 ID 로 교체한 채 캐시에 남겨두기 때문에
        #   이미 target_id 를 반환할 수 있다. 대신 Flask-Login 이 로그인 시
        #   session 에 저장한 '_user_id' (원본 로그인 ID) 로 검증한다.
        real_login_id = session.get('_user_id')
        if not real_login_id or str(real_login_id) != str(admin_id):
            session.pop('view_as_user_id', None)
            session.pop('view_as_admin_id', None)
            return
        if not app.supabase:
            return
        try:
            res = app.supabase.table('users').select('*').eq('id', view_as_uid).execute()
            if not res.data:
                return
            target = res.data[0]

            # ── current_user 를 대상 유저 정보로 완전 교체 ─────────────
            # id 를 바꾸면 brand/creation/point 등 모든 DB 쿼리가
            # 대상 유저 기준으로 실행됨
            current_user.id            = str(target['id'])
            current_user.email         = target.get('email', '')
            current_user.name          = target.get('name') or target.get('email', '').split('@')[0]
            current_user.plan_type     = target.get('plan_type', 'free')
            current_user.operator_id   = target.get('operator_id')
            current_user._is_active    = target.get('is_active', True)
            current_user.last_seen_at  = target.get('last_seen_at')
            # site_role 은 건드리지 않음 → is_superadmin 유지 → 어드민 메뉴·라우트 정상 접근
            current_user._view_as_mode = True

            g.view_as_mode       = True
            g.view_as_admin_id   = admin_id
            g.view_as_user_id    = view_as_uid
            g.view_as_user_email = target.get('email', view_as_uid)
            g.view_as_user_name  = target.get('name') or target.get('email', '')
        except Exception as e:
            app.logger.warning(f'[view_as] 대상 유저 로드 실패: {e}')

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

        # HSTS — 프로덕션 전용 (HTTPS 강제, 1년)
        if not app.debug:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

        # Content-Security-Policy
        # 인라인 스크립트/스타일이 광범위하게 사용되므로 unsafe-inline 허용,
        # 외부 리소스는 명시된 출처만 허용
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "cdn.jsdelivr.net cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' "
            "cdn.jsdelivr.net cdnjs.cloudflare.com fonts.googleapis.com; "
            "font-src 'self' fonts.gstatic.com cdn.jsdelivr.net cdnjs.cloudflare.com data:; "
            "img-src 'self' data: blob: *.supabase.co *.supabase.in "
            "storage.googleapis.com cdnjs.cloudflare.com "
            "*.fal.media fal.media *.fal.run; "
            "media-src 'self' blob: *.supabase.co *.supabase.in "
            "*.fal.media fal.media; "
            "connect-src 'self' *.supabase.co *.supabase.in "
            "cdn.jsdelivr.net cdnjs.cloudflare.com; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )
        response.headers['Content-Security-Policy'] = csp
        return response


def _init_redis(app):
    redis_url = app.config.get('REDIS_URL')
    if not redis_url:
        return
    try:
        import redis as redis_lib
        from flask_session import Session
        r = redis_lib.from_url(redis_url, decode_responses=False)
        app.config['SESSION_REDIS'] = r
        Session(app)
        app.logger.info('[Redis] 연결 완료')
    except Exception as e:
        app.logger.warning(f'[Redis] 연결 실패 — filesystem 세션 사용: {e}')


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
