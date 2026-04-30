"""매실 스튜디오 - Supabase 헬퍼"""
from flask import current_app


def get_supabase():
    """현재 앱의 Supabase 클라이언트 반환"""
    return current_app.supabase


class DemoProxy:
    """Supabase 미연결 시 로컬 테스트용 프록시"""
    def __getattr__(self, attr):
        if attr.startswith(('list_', 'query_')):
            return lambda *a, **kw: []
        if attr.startswith('count_'):
            return lambda *a, **kw: 0
        if attr.startswith('get_'):
            return lambda *a, **kw: None
        return lambda *a, **kw: None
