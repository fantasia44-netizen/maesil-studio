"""IP 기반 레이트 리밋 (Redis 우선, 인메모리 폴백)"""
import time
import threading
from collections import defaultdict

_lock = threading.Lock()
_attempts: dict = defaultdict(list)


class InMemoryRateLimiter:
    def is_rate_limited(self, key: str, max_attempts: int, window_seconds: int) -> bool:
        now = time.time()
        with _lock:
            _attempts[key] = [t for t in _attempts[key] if now - t < window_seconds]
            if len(_attempts[key]) >= max_attempts:
                return True
            _attempts[key].append(now)
            return False


class RedisRateLimiter:
    def __init__(self, redis_client):
        self._r = redis_client

    def is_rate_limited(self, key: str, max_attempts: int, window_seconds: int) -> bool:
        rkey = f'rl:{key}'
        pipe = self._r.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, window_seconds)
        count, _ = pipe.execute()
        return count > max_attempts


_limiter = None


def get_rate_limiter():
    global _limiter
    if _limiter is not None:
        return _limiter
    try:
        from flask import current_app
        redis_client = current_app.config.get('SESSION_REDIS')
        if redis_client:
            _limiter = RedisRateLimiter(redis_client)
            return _limiter
    except Exception:
        pass
    _limiter = InMemoryRateLimiter()
    return _limiter


# ── AI 생성 엔드포인트용 사용자 레이트 리밋 헬퍼 ─────────────────────
# 사용법: err = check_ai_rate_limit('shorts', max_per_hour=5)
#         if err: return jsonify(error=err), 429

def check_ai_rate_limit(resource: str, max_per_hour: int = 20) -> 'str | None':
    """현재 로그인 유저 기준 AI 생성 레이트 리밋 체크.

    초과 시 오류 메시지 반환, 통과 시 None 반환.
    resource: 'shorts' | 'image' | 'instagram' | 'blog' 등 엔드포인트 구분자
    """
    try:
        from flask_login import current_user
        if not current_user or not current_user.is_authenticated:
            return None  # 비인증 → @login_required에서 처리
        user_id = str(current_user.get_id())
        key = f'ai:{resource}:{user_id}'
        limited = get_rate_limiter().is_rate_limited(key, max_per_hour, 3600)
        if limited:
            return f'요청이 너무 많습니다. 1시간에 최대 {max_per_hour}회 생성 가능합니다.'
    except Exception:
        pass  # 레이트 리밋 오류는 무시 (서비스 중단 방지)
    return None
