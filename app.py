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
        return {
            'app_name': app.config.get('APP_NAME', '매실 스튜디오'),
            'PLAN_FEATURES': PLAN_FEATURES,
            'POINT_COSTS': POINT_COSTS,
            'CREATION_LABELS': CREATION_LABELS,
        }


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

    @login_manager.user_loader
    def load_user(user_id):
        import time
        cached = _user_cache.get(user_id)
        if cached and time.time() - cached['ts'] < CACHE_TTL:
            return cached['user']

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

    @app.after_request
    def security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
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
