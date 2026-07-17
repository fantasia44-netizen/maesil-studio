"""워드프레스 연동 — 연결 정보 저장/조회 헬퍼.

저장: wordpress_connections 테이블.
  - 개인(B2C): user_id 단위 1개
  - 팀(operator): operator_id 단위 1개 (팀원 전체 공유)
  - 브랜드(brand_id 지정 시): 브랜드 단위 1개, 폴백 없음 — 경험담 블로그가 쓰는
    위 두 방식과 완전히 분리된 별도 행. 일반 블로그의 브랜드별 발행 전용.

앱 비밀번호 평문은 Fernet (services.crypto) 으로 암호화.
maesil_insight_connection 과 동일한 정책/구조.
"""
from __future__ import annotations

import logging
import re

from flask import current_app

from services.crypto import decrypt_value, encrypt_value
from services.wordpress_client import WordPressClient
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)

_TABLE = 'wordpress_connections'


# ─────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────

def _get_by_user(sb, user_id: str) -> dict | None:
    try:
        res = (sb.table(_TABLE)
               .select('*')
               .eq('user_id', str(user_id))
               .limit(1)
               .execute())
        return (res.data[0] if res and res.data else None)
    except Exception as e:
        logger.warning(f'[WPConn] user 조회 실패: {e}')
        return None


def _get_by_operator(sb, operator_id: str) -> dict | None:
    try:
        res = (sb.table(_TABLE)
               .select('*')
               .eq('operator_id', str(operator_id))
               .limit(1)
               .execute())
        return (res.data[0] if res and res.data else None)
    except Exception as e:
        logger.warning(f'[WPConn] operator 조회 실패: {e}')
        return None


def _get_by_brand(sb, brand_id: str) -> dict | None:
    """브랜드 전용 연결 조회. 폴백 없음 — 없으면 그대로 None."""
    try:
        res = (sb.table(_TABLE)
               .select('*')
               .eq('brand_id', str(brand_id))
               .limit(1)
               .execute())
        return (res.data[0] if res and res.data else None)
    except Exception as e:
        logger.warning(f'[WPConn] brand 조회 실패: {e}')
        return None


def _normalize_site(url: str) -> str:
    """'example.com' → 'https://example.com', 뒤 슬래시 제거."""
    url = (url or '').strip().rstrip('/')
    if not url:
        raise ValueError('site_url is required')
    if not re.match(r'^https?://', url, re.IGNORECASE):
        url = 'https://' + url
    return url


def _password_prefix(pw: str) -> str:
    """마스킹 표시용 prefix (첫 4자)."""
    pw = (pw or '').replace(' ', '')
    return pw[:4] if pw else ''


# ─────────────────────────────────────────────────────────────
# 공개 조회 API
# ─────────────────────────────────────────────────────────────

def get_connection(user_id, operator_id=None, brand_id=None) -> dict | None:
    """사용자의 워드프레스 연결 row 반환 (없으면 None).

    brand_id 있음: 그 브랜드 전용 연결만 조회 — 폴백 없음(없으면 None).
    brand_id 없음(기존 동작): 팀 모드(operator_id 있음)는 operator 단위 공유 연결
      우선 조회, 개인 모드는 user_id 로만 조회.
    """
    sb = current_app.supabase
    if not sb:
        return None
    if brand_id:
        return _get_by_brand(sb, brand_id)
    if operator_id:
        conn = _get_by_operator(sb, operator_id)
        if conn:
            return conn
    return _get_by_user(sb, user_id)


def get_client_for_user(user_id, operator_id=None, brand_id=None) -> WordPressClient | None:
    """사용자의 자격증명으로 초기화된 클라이언트 반환 (없으면 None)."""
    conn = get_connection(user_id, operator_id=operator_id, brand_id=brand_id)
    if not conn:
        return None
    enc = conn.get('app_password_encrypted') or ''
    if not enc:
        return None
    try:
        pw = decrypt_value(enc)
    except Exception as e:
        logger.warning(f'[WPConn] 복호화 실패: {e}')
        return None
    return WordPressClient(
        conn.get('site_url') or '',
        conn.get('wp_username') or '',
        pw,
        use_rest_route=bool(conn.get('use_rest_route')),
    )


def is_connected(user_id, operator_id=None, brand_id=None) -> bool:
    return get_connection(user_id, operator_id=operator_id, brand_id=brand_id) is not None


# ─────────────────────────────────────────────────────────────
# 저장 / 갱신 / 해제
# ─────────────────────────────────────────────────────────────

def save_connection(user_id, *, site_url: str, username: str, app_password: str,
                    me: dict, use_rest_route: bool = False, operator_id=None,
                    brand_id=None) -> dict:
    """자격증명 + /users/me 응답 캐시를 upsert.

    brand_id 있음 → 브랜드 단위로 저장 (그 브랜드 전용, 폴백 없음).
    brand_id 없음(기존 동작): operator_id 있으면 operator 단위(팀원 공유),
      없으면 user 단위(개인).
    """
    sb = current_app.supabase
    now_iso = now_kst().isoformat()
    row = {
        'user_id':                str(user_id),
        'site_url':               site_url,
        'wp_username':            username,
        'app_password_encrypted': encrypt_value(app_password),
        'password_prefix':        _password_prefix(app_password),
        'wp_display_name':        me.get('name') or me.get('slug'),
        'wp_user_id':             me.get('id'),
        'use_rest_route':         bool(use_rest_route),
        'connected_at':           now_iso,
        'last_verified_at':       now_iso,
        'last_used_at':           now_iso,
        'last_error':             None,
    }

    if brand_id:
        row['brand_id'] = str(brand_id)
        if operator_id:
            row['operator_id'] = str(operator_id)
        res = (sb.table(_TABLE)
               .upsert(row, on_conflict='brand_id')
               .execute())
    elif operator_id:
        row['operator_id'] = str(operator_id)
        existing = _get_by_operator(sb, operator_id)
        if existing:
            res = (sb.table(_TABLE)
                   .update(row)
                   .eq('operator_id', str(operator_id))
                   .execute())
        else:
            res = sb.table(_TABLE).insert(row).execute()
    else:
        res = (sb.table(_TABLE)
               .upsert(row, on_conflict='user_id')
               .execute())

    return (res.data[0] if res and res.data else row)


def disconnect(user_id, operator_id=None, brand_id=None) -> None:
    """연결 해제 (row 삭제)."""
    sb = current_app.supabase
    if not sb:
        return
    if brand_id:
        sb.table(_TABLE).delete().eq('brand_id', str(brand_id)).execute()
    elif operator_id:
        sb.table(_TABLE).delete().eq('operator_id', str(operator_id)).execute()
    else:
        sb.table(_TABLE).delete().eq('user_id', str(user_id)).execute()


def mark_used(user_id, operator_id=None, brand_id=None) -> None:
    """발행 시 last_used_at 업데이트 — 실패해도 무시."""
    sb = current_app.supabase
    if not sb:
        return
    try:
        q = sb.table(_TABLE).update({'last_used_at': now_kst().isoformat()})
        if brand_id:
            q = q.eq('brand_id', str(brand_id))
        elif operator_id:
            q = q.eq('operator_id', str(operator_id))
        else:
            q = q.eq('user_id', str(user_id))
        q.execute()
    except Exception:
        pass


def mark_error(user_id, message: str, operator_id=None, brand_id=None) -> None:
    """오류 발생 시 last_error 기록 (UI 표시용)."""
    sb = current_app.supabase
    if not sb:
        return
    try:
        q = sb.table(_TABLE).update({'last_error': (message or '')[:500]})
        if brand_id:
            q = q.eq('brand_id', str(brand_id))
        elif operator_id:
            q = q.eq('operator_id', str(operator_id))
        else:
            q = q.eq('user_id', str(user_id))
        q.execute()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 검증 + 저장 (한 번에)
# ─────────────────────────────────────────────────────────────

def verify_and_save(user_id, *, site_url: str, username: str, app_password: str,
                    operator_id=None, brand_id=None) -> dict:
    """/users/me 호출로 자격증명 검증 → 성공 시 저장 → connection row 반환.

    실패 시 services.wordpress_client.WordPressError 가 발생하므로
    호출자에서 try/except 로 받아 사용자에게 메시지 표시.
    """
    site = _normalize_site(site_url)
    username = (username or '').strip()
    app_password = (app_password or '').strip()
    if not username or not app_password:
        raise ValueError('username and app_password are required')

    client = WordPressClient(site, username, app_password)
    me = client.verify()  # 401/403 → WordPressError
    return save_connection(
        user_id,
        site_url=site, username=username, app_password=app_password,
        me=me, use_rest_route=client.use_rest_route, operator_id=operator_id,
        brand_id=brand_id,
    )
