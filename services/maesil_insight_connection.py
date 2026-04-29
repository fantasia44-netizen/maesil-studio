"""매실 인사이트 연동 — 토큰 저장/조회 헬퍼.

저장: maesil_insight_connections 테이블 (사용자당 1개).
토큰 평문은 Fernet (services.crypto) 으로 암호화.
"""
from __future__ import annotations

import logging

from flask import current_app

from services.crypto import decrypt_value, encrypt_value
from services.maesil_insight_client import MaesilInsightClient
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 조회
# ─────────────────────────────────────────────────────────────

def get_connection(user_id) -> dict | None:
    """사용자의 인사이트 연결 row 반환 (없으면 None)."""
    sb = current_app.supabase
    if not sb:
        return None
    try:
        res = (sb.table('maesil_insight_connections')
               .select('*')
               .eq('user_id', str(user_id))
               .limit(1)
               .execute())
        return (res.data[0] if res and res.data else None)
    except Exception as e:
        logger.warning(f'[InsightConn] 조회 실패: {e}')
        return None


def get_connection_token(user_id) -> str | None:
    """평문 토큰 반환 (인사이트 API 호출용)."""
    conn = get_connection(user_id)
    if not conn:
        return None
    enc = conn.get('token_encrypted') or ''
    if not enc:
        return None
    try:
        return decrypt_value(enc)
    except Exception as e:
        logger.warning(f'[InsightConn] 복호화 실패: {e}')
        return None


def get_client_for_user(user_id) -> MaesilInsightClient | None:
    """사용자의 토큰으로 초기화된 클라이언트 반환 (없으면 None)."""
    token = get_connection_token(user_id)
    if not token:
        return None
    return MaesilInsightClient(token)


def is_connected(user_id) -> bool:
    return get_connection(user_id) is not None


# ─────────────────────────────────────────────────────────────
# 저장 / 갱신 / 해제
# ─────────────────────────────────────────────────────────────

def _token_prefix(token: str) -> str:
    """마스킹 표시용 prefix (첫 11자, 보통 'mi_xxxxxxxx')."""
    return token[:11] if token else ''


def save_connection(user_id, *, token: str, me: dict) -> dict:
    """토큰 + /me 응답 캐시를 upsert.

    me 는 인사이트 GET /me 응답 (operator_id/operator_name/plan/scopes/...).
    """
    sb = current_app.supabase
    now_iso = now_kst().isoformat()
    row = {
        'user_id':               str(user_id),
        'token_encrypted':       encrypt_value(token),
        'token_prefix':          _token_prefix(token),
        'insight_operator_id':   me.get('operator_id'),
        'insight_operator_name': me.get('operator_name'),
        'insight_plan':          me.get('plan'),
        'scopes':                me.get('scopes') or [],
        'expires_at':            me.get('expires_at'),
        'connected_at':          now_iso,
        'last_verified_at':      now_iso,
        'last_used_at':          now_iso,
        'last_error':            None,
    }
    res = (sb.table('maesil_insight_connections')
           .upsert(row, on_conflict='user_id')
           .execute())
    return (res.data[0] if res and res.data else row)


def disconnect(user_id) -> None:
    """연결 해제 (row 삭제)."""
    sb = current_app.supabase
    if not sb:
        return
    sb.table('maesil_insight_connections') \
      .delete() \
      .eq('user_id', str(user_id)) \
      .execute()


def mark_used(user_id) -> None:
    """API 호출 시 last_used_at 업데이트 — 실패해도 무시."""
    sb = current_app.supabase
    if not sb:
        return
    try:
        sb.table('maesil_insight_connections') \
          .update({'last_used_at': now_kst().isoformat()}) \
          .eq('user_id', str(user_id)) \
          .execute()
    except Exception:
        pass


def mark_error(user_id, message: str) -> None:
    """인증 오류 발생 시 last_error 기록 (UI 표시용)."""
    sb = current_app.supabase
    if not sb:
        return
    try:
        sb.table('maesil_insight_connections') \
          .update({'last_error': (message or '')[:500]}) \
          .eq('user_id', str(user_id)) \
          .execute()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 검증 + 저장 (한 번에)
# ─────────────────────────────────────────────────────────────

def verify_and_save(user_id, token: str) -> dict:
    """/me 호출로 토큰 검증 → 성공 시 저장 → connection row 반환.

    실패 시 services.maesil_insight_client.MaesilInsightError 가 발생하므로
    호출자에서 try/except 로 받아 사용자에게 메시지 표시.
    """
    if not token or not token.strip():
        raise ValueError('token is required')
    client = MaesilInsightClient(token.strip())
    me = client.verify()  # 401 → MaesilInsightError('unauthorized', 401)
    return save_connection(user_id, token=token.strip(), me=me)
