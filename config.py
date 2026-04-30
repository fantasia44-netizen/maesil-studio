"""매실 스튜디오 - 환경별 설정"""
import os


def _get_secret_key():
    _secret = os.environ.get('SECRET_KEY')
    if not _secret:
        if os.environ.get('RENDER') or os.environ.get('FLASK_ENV') == 'production':
            raise ValueError("SECRET_KEY 환경변수가 설정되지 않았습니다")
        _secret = 'dev-only-unsafe-key-do-not-use-in-production'
    return _secret


class Config:
    SECRET_KEY = _get_secret_key()

    # Supabase
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_ANON_KEY')
    SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY')

    # API 키 암호화
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY')

    # Redis (세션 + 레이트리밋)
    REDIS_URL = os.environ.get('REDIS_URL', '')
    SESSION_TYPE = 'redis' if os.environ.get('REDIS_URL') else 'filesystem'
    SESSION_KEY_PREFIX = 'creator:'
    SESSION_USE_SIGNER = True

    # 세션
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400  # 24시간
    SESSION_INACTIVITY_TIMEOUT = 120   # 분

    # 로그인 보안
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCKOUT_MINUTES = 15
    IP_RATE_LIMIT_ATTEMPTS = 20
    IP_RATE_LIMIT_WINDOW = 900  # 15분

    # 파일 업로드
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB

    # 앱 정보
    APP_NAME = os.environ.get('APP_NAME', '매실 스튜디오')
    APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')


    JSON_AS_ASCII = False


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_INACTIVITY_TIMEOUT = 120
    SESSION_COOKIE_SECURE = True
    LOGIN_MAX_ATTEMPTS = 3
