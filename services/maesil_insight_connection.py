"""매실 인사이트 연동 — 토큰 저장/조회 헬퍼.

저장: maesil_insight_connections 테이블.
  - 개인(B2C): user_id 단위 1개
  - 팀(operator): operator_id 단위 1개 (팀원 전체 공유)

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
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────

def _get_by_user(sb, user_id: str) -> dict | None:
    try:
        res = (sb.table('maesil_insight_connections')
               .select('*')
               .eq('user_id', str(user_id))
               .limit(1)
               .execute())
        return (res.data[0] if res and res.data else None)
    except Exception as e:
        logger.warning(f'[InsightConn] user 조회 실패: {e}')
        return None


def _get_by_operator(sb, operator_id: str) -> dict | None:
    try:
        res = (sb.table('maesil_insight_connections')
               .select('*')
               .eq('operator_id', str(operator_id))
               .limit(1)
               .execute())
        return (res.data[0] if res and res.data else None)
    except Exception as e:
        logger.warning(f'[InsightConn] operator 조회 실패: {e}')
        return None


# ─────────────────────────────────────────────────────────────
# 공개 조회 API
# ─────────────────────────────────────────────────────────────

def get_connection(user_id, operator_id=None) -> dict | None:
    """사용자의 인사이트 연결 row 반환 (없으면 None).

    팀 모드(operator_id 있음):
        → operator 단위 공유 연결 우선 조회.
          (어느 팀원이 연결해도 팀 전체에 공유)
    개인 모드(operator_id 없음):
        → user_id 로만 조회.
    """
    sb = current_app.supabase
    if not sb:
        return None

    if operator_id:
        conn = _get_by_operator(sb, operator_id)
        if conn:
            return conn

    # 개인 연결 폴백 (operator 없는 경우, 또는 operator 연결이 아직 없는 경우)
    return _get_by_user(sb, user_id)


def get_connection_token(user_id, operator_id=None) -> str | None:
    """평문 토큰 반환 (인사이트 API 호출용)."""
    conn = get_connection(user_id, operator_id=operator_id)
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


def get_client_for_user(user_id, operator_id=None) -> MaesilInsightClient | None:
    """사용자의 토큰으로 초기화된 클라이언트 반환 (없으면 None)."""
    token = get_connection_token(user_id, operator_id=operator_id)
    if not token:
        return None
    return MaesilInsightClient(token)


def is_connected(user_id, operator_id=None) -> bool:
    return get_connection(user_id, operator_id=operator_id) is not None


# ─────────────────────────────────────────────────────────────
# 저장 / 갱신 / 해제
# ─────────────────────────────────────────────────────────────

def _token_prefix(token: str) -> str:
    """마스킹 표시용 prefix (첫 11자, 보통 'mi_xxxxxxxx')."""
    return token[:11] if token else ''


def save_connection(user_id, *, token: str, me: dict, operator_id=None) -> dict:
    """토큰 + /me 응답 캐시를 upsert.

    operator_id 있음 → operator 단위로 저장 (팀원 공유).
    operator_id 없음 → user 단위로 저장 (개인).
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

    if operator_id:
        # 팀 연결: operator_id 단위로 upsert
        row['operator_id'] = str(operator_id)
        existing = _get_by_operator(sb, operator_id)
        if existing:
            # 기존 operator 연결 업데이트 (user_id 는 마지막 연결자 user 로 갱신)
            update_fields = {k: v for k, v in row.items()}
            res = (sb.table('maesil_insight_connections')
                   .update(update_fields)
                   .eq('operator_id', str(operator_id))
                   .execute())
        else:
            res = sb.table('maesil_insight_connections').insert(row).execute()
    else:
        # 개인 연결: user_id 단위로 upsert
        res = (sb.table('maesil_insight_connections')
               .upsert(row, on_conflict='user_id')
               .execute())

    return (res.data[0] if res and res.data else row)


def disconnect(user_id, operator_id=None) -> None:
    """연결 해제 (row 삭제).

    팀 모드: operator 단위 연결 삭제.
    개인 모드: user 단위 연결 삭제.
    """
    sb = current_app.supabase
    if not sb:
        return
    if operator_id:
        (sb.table('maesil_insight_connections')
         .delete()
         .eq('operator_id', str(operator_id))
         .execute())
    else:
        (sb.table('maesil_insight_connections')
         .delete()
         .eq('user_id', str(user_id))
         .execute())


def mark_used(user_id, operator_id=None) -> None:
    """API 호출 시 last_used_at 업데이트 — 실패해도 무시."""
    sb = current_app.supabase
    if not sb:
        return
    try:
        q = sb.table('maesil_insight_connections').update(
            {'last_used_at': now_kst().isoformat()}
        )
        if operator_id:
            q = q.eq('operator_id', str(operator_id))
        else:
            q = q.eq('user_id', str(user_id))
        q.execute()
    except Exception:
        pass


def mark_error(user_id, message: str, operator_id=None) -> None:
    """인증 오류 발생 시 last_error 기록 (UI 표시용)."""
    sb = current_app.supabase
    if not sb:
        return
    try:
        q = sb.table('maesil_insight_connections').update(
            {'last_error': (message or '')[:500]}
        )
        if operator_id:
            q = q.eq('operator_id', str(operator_id))
        else:
            q = q.eq('user_id', str(user_id))
        q.execute()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 검증 + 저장 (한 번에)
# ─────────────────────────────────────────────────────────────

def verify_and_save(user_id, token: str, operator_id=None) -> dict:
    """/me 호출로 토큰 검증 → 성공 시 저장 → connection row 반환.

    실패 시 services.maesil_insight_client.MaesilInsightError 가 발생하므로
    호출자에서 try/except 로 받아 사용자에게 메시지 표시.
    """
    if not token or not token.strip():
        raise ValueError('token is required')
    client = MaesilInsightClient(token.strip())
    me = client.verify()  # 401 → MaesilInsightError('unauthorized', 401)
    return save_connection(user_id, token=token.strip(), me=me, operator_id=operator_id)
