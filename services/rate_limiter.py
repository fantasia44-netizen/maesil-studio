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
