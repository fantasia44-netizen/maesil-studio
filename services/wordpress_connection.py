"""워드프레스 연동 — 연결 정보 저장/조회 헬퍼.

저장: wordpress_connections 테이블. 브랜드 단위 1개(폴백 없음) — 브랜드마다
서로 다른 워드프레스 사이트에 발행할 수 있다(예: 매실 → blog.maesil.net,
배마마 → blog.baemama.co.kr). 경험담 블로그/일반 블로그 모두 이 브랜드 단위
연결을 공유해서 쓴다.

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

def get_connection(brand_id) -> dict | None:
    """브랜드의 워드프레스 연결 row 반환 (없으면 None). 폴백 없음."""
    sb = current_app.supabase
    if not sb or not brand_id:
        return None
    return _get_by_brand(sb, brand_id)


def get_client_for_user(brand_id) -> WordPressClient | None:
    """브랜드의 자격증명으로 초기화된 클라이언트 반환 (없으면 None)."""
    conn = get_connection(brand_id)
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


def is_connected(brand_id) -> bool:
    return get_connection(brand_id) is not None


# ─────────────────────────────────────────────────────────────
# 저장 / 갱신 / 해제
# ─────────────────────────────────────────────────────────────

def save_connection(user_id, *, site_url: str, username: str, app_password: str,
                    me: dict, brand_id, use_rest_route: bool = False,
                    operator_id=None) -> dict:
    """자격증명 + /users/me 응답 캐시를 브랜드 단위로 upsert."""
    sb = current_app.supabase
    now_iso = now_kst().isoformat()
    row = {
        'user_id':                str(user_id),
        'brand_id':               str(brand_id),
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
    if operator_id:
        row['operator_id'] = str(operator_id)

    res = (sb.table(_TABLE)
           .upsert(row, on_conflict='brand_id')
           .execute())
    return (res.data[0] if res and res.data else row)


def disconnect(brand_id) -> None:
    """연결 해제 (row 삭제)."""
    sb = current_app.supabase
    if not sb:
        return
    sb.table(_TABLE).delete().eq('brand_id', str(brand_id)).execute()


def mark_used(brand_id) -> None:
    """발행 시 last_used_at 업데이트 — 실패해도 무시."""
    sb = current_app.supabase
    if not sb:
        return
    try:
        sb.table(_TABLE).update({'last_used_at': now_kst().isoformat()}) \
          .eq('brand_id', str(brand_id)).execute()
    except Exception:
        pass


def mark_error(brand_id, message: str) -> None:
    """오류 발생 시 last_error 기록 (UI 표시용)."""
    sb = current_app.supabase
    if not sb:
        return
    try:
        sb.table(_TABLE).update({'last_error': (message or '')[:500]}) \
          .eq('brand_id', str(brand_id)).execute()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 검증 + 저장 (한 번에)
# ─────────────────────────────────────────────────────────────

def verify_and_save(user_id, *, site_url: str, username: str, app_password: str,
                    brand_id, operator_id=None) -> dict:
    """/users/me 호출로 자격증명 검증 → 성공 시 브랜드 단위로 저장 → connection row 반환.

    실패 시 services.wordpress_client.WordPressError 가 발생하므로
    호출자에서 try/except 로 받아 사용자에게 메시지 표시.
    """
    site = _normalize_site(site_url)
    username = (username or '').strip()
    app_password = (app_password or '').strip()
    if not username or not app_password:
        raise ValueError('username and app_password are required')
    if not brand_id:
        raise ValueError('brand_id is required')

    client = WordPressClient(site, username, app_password)
    me = client.verify()  # 401/403 → WordPressError
    return save_connection(
        user_id,
        site_url=site, username=username, app_password=app_password,
        me=me, use_rest_route=client.use_rest_route,
        brand_id=brand_id, operator_id=operator_id,
    )
