"""포인트 잔액 조회 / 차감 / 충전 — 팀(Operator) 풀 지원.

핵심 규칙:
  - user.operator_id 가 있으면  → operator 풀 (팀 공유)
  - user.operator_id 가 없으면 → user 풀 (개인 계정 종전 동작)

행 저장 시 user_id 는 항상 채움(누가 사용/생성했는지 감사). operator 모드는
operator_id 도 함께 채움.

호환성:
  기존 API (use_points/add_points/get_balance/get_ledger) 의 첫 인자는 user_id
  문자열이지만, 이제 User 객체도 받는다. 객체면 operator_id 자동 추론.

포인트 만료 규칙 (2025-05~ 적용):
  - 웰컴 포인트   : 지급일로부터 30일
  - 구독 포인트   : 구독 기간 종료일 (billing period end)
  - 구매 포인트   : 구매일로부터 365일
  - 관리자 지급   : 무기한 (expires_at=None)

만료 처리:
  get_balance() 호출 시 lazily 만료 확정(materialize).
  expires_at < NOW() & remaining > 0 인 항목에 대해
  'expire' 타입 음수 트랜잭션을 삽입하고 remaining = 0 으로 갱신.
"""
import logging
from typing import Any
from flask import current_app
from flask_login import current_user
from models import POINT_COSTS, CREATION_LABELS
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


class InsufficientPoints(Exception):
    pass


# ─────────────────────────────────────────────────────────────
# Owner 해석 헬퍼
# ─────────────────────────────────────────────────────────────

def _resolve_owner(arg: Any) -> tuple[str, str | None]:
    """다양한 입력을 (user_id, operator_id) 로 정규화.

    arg 가 User 객체 / dict / str(=user_id) 모두 허용.
    user_id 만 받았을 땐 DB 에서 operator_id 조회.
    """
    user_id: str = ''
    operator_id: str | None = None

    # User 객체
    if hasattr(arg, 'id') and (hasattr(arg, 'operator_id') or hasattr(arg, 'is_authenticated')):
        user_id = str(getattr(arg, 'id', '') or '')
        op = getattr(arg, 'operator_id', None)
        operator_id = str(op) if op else None
        return user_id, operator_id

    # dict
    if isinstance(arg, dict):
        user_id = str(arg.get('id') or arg.get('user_id') or '')
        op = arg.get('operator_id')
        operator_id = str(op) if op else None
        return user_id, operator_id

    # str (user_id) — operator_id 를 DB 에서 조회 (best-effort)
    user_id = str(arg or '')
    if not user_id:
        return '', None
    try:
        sb = current_app.supabase
        if sb:
            r = (sb.table('users').select('operator_id')
                 .eq('id', user_id).limit(1).execute())
            if r and r.data:
                op = r.data[0].get('operator_id')
                operator_id = str(op) if op else None
    except Exception as e:
        logger.debug(f'[POINT] user operator_id 조회 실패({user_id}): {e}')
    return user_id, operator_id


def _scope_filter(query, operator_id: str | None, user_id: str):
    """operator_id 가 있으면 operator 풀로, 없으면 user 풀로 필터.

    포인트/구독/결제 모두 동일한 분기. 'IS NULL' 필터를 함께 걸어
    operator 풀 안에 포함된 멤버의 개인 풀 잔여 행이 섞이지 않게 함.
    """
    if operator_id:
        return query.eq('operator_id', operator_id)
    # 개인 풀: operator_id 없는 행만
    return query.is_('operator_id', 'null').eq('user_id', user_id)


# ─────────────────────────────────────────────────────────────
# 만료 처리 (lazy materialization)
# ─────────────────────────────────────────────────────────────

def _expire_stale_points(user_id: str, operator_id: str | None, supabase) -> None:
    """만료된 포인트 버킷을 'expire' 트랜잭션으로 확정.

    expires_at < NOW() 이고 remaining > 0 인 양수 항목 → 음수 expire 행 삽입 후
    해당 항목의 remaining = 0 으로 갱신.

    이 함수는 get_balance() 호출 시 자동으로 실행된다.
    """
    now = now_kst().isoformat()
    try:
        q = supabase.table('point_ledger').select('id, remaining, expires_at')
        q = _scope_filter(q, operator_id, user_id)
        # expires_at < now AND remaining > 0
        result = q.gt('remaining', 0).lt('expires_at', now).execute()
        expired_rows = result.data or []
        if not expired_rows:
            return

        for row in expired_rows:
            remaining = row.get('remaining') or 0
            if remaining <= 0:
                continue

            # 중복 만료 방지: remaining 을 0 으로 먼저 설정 후 실제 잔액 차감
            # (동시 요청이 와도 두 번째 요청은 remaining=0 이므로 이미 위 필터에서 제외됨)
            upd = supabase.table('point_ledger').update(
                {'remaining': 0}
            ).eq('id', row['id']).eq('remaining', remaining).execute()
            # 업데이트된 행이 없으면 다른 요청이 먼저 처리한 것 — 스킵
            if not (upd.data):
                logger.debug(f'[POINT] 만료 경쟁 스킵: row_id={row["id"]}')
                continue

            # 현재 잔액 직접 조회 (재귀 방지 — get_balance 미사용)
            bal_q = supabase.table('point_ledger').select('balance')
            bal_q = _scope_filter(bal_q, operator_id, user_id)
            bal_r = bal_q.order('created_at', desc=True).limit(1).execute()
            current_bal = bal_r.data[0].get('balance', 0) if bal_r.data else 0

            new_balance = max(0, current_bal - remaining)

            expire_row = {
                'user_id': user_id,
                'type': 'expire',
                'amount': -remaining,
                'balance': new_balance,
                'ref_id': str(row['id']),
                'note': '포인트 만료',
                'created_at': now_kst().isoformat(),
                'remaining': None,
                'expires_at': None,
            }
            if operator_id:
                expire_row['operator_id'] = operator_id

            supabase.table('point_ledger').insert(expire_row).execute()

            logger.info(f'[POINT] 만료 확정: user={user_id} row_id={row["id"]} -{remaining}P')

    except Exception as e:
        logger.error(f'[POINT] _expire_stale_points error: {e}')


def _consume_from_buckets(user_id: str, operator_id: str | None, amount: int, supabase) -> None:
    """포인트 사용 시 만료 임박 버킷부터 remaining 을 차감.

    이 함수 덕분에 나중에 버킷 만료 시 이미 소비된 부분은 제외하고
    실제 잔여분만 expire 처리한다.
    """
    if amount <= 0:
        return
    try:
        now = now_kst().isoformat()
        q = supabase.table('point_ledger').select('id, remaining, expires_at')
        q = _scope_filter(q, operator_id, user_id)
        result = q.gt('remaining', 0).execute()
        rows = result.data or []

        # Python 에서 정렬: 만료 임박(expires_at ASC, None은 마지막) 순
        rows.sort(key=lambda r: (r.get('expires_at') is None, r.get('expires_at') or ''))

        # 만료된 항목 제외 (아직 _expire_stale_points 호출 전 엣지케이스 방어)
        rows = [r for r in rows if not r.get('expires_at') or r['expires_at'] > now]

        to_consume = amount
        for row in rows:
            if to_consume <= 0:
                break
            row_remaining = row.get('remaining') or 0
            consume = min(row_remaining, to_consume)
            supabase.table('point_ledger').update(
                {'remaining': row_remaining - consume}
            ).eq('id', row['id']).execute()
            to_consume -= consume

    except Exception as e:
        # non-critical: remaining 추적 실패해도 잔액 계산 자체는 영향 없음
        logger.warning(f'[POINT] _consume_from_buckets error (무시): {e}')


# ─────────────────────────────────────────────────────────────
# 잔액 / 이력
# ─────────────────────────────────────────────────────────────

def get_balance(owner: Any) -> int:
    """현재 유효 잔액 — 만료 포인트 자동 처리 후 반환."""
    user_id, operator_id = _resolve_owner(owner)
    supabase = current_app.supabase
    if not supabase or not user_id:
        return 0
    try:
        # 만료 포인트 먼저 확정
        _expire_stale_points(user_id, operator_id, supabase)

        q = supabase.table('point_ledger').select('balance')
        q = _scope_filter(q, operator_id, user_id)
        row = q.order('created_at', desc=True).limit(1).execute()
        return (row.data[0].get('balance', 0)) if row.data else 0
    except Exception as e:
        logger.error(f'[POINT] get_balance error: {e}')
        return 0


def get_ledger(owner: Any, limit: int = 50) -> list:
    """포인트 입출내역 조회."""
    user_id, operator_id = _resolve_owner(owner)
    supabase = current_app.supabase
    if not supabase or not user_id:
        return []
    try:
        q = supabase.table('point_ledger').select('*')
        q = _scope_filter(q, operator_id, user_id)
        result = q.order('created_at', desc=True).limit(limit).execute()
        return result.data or []
    except Exception as e:
        logger.error(f'[POINT] get_ledger error: {e}')
        return []


def get_expiry_summary(owner: Any) -> list[dict]:
    """만료 예정 포인트 요약 — 잔액 페이지 표시용.

    Returns: [{'type': ..., 'amount': ..., 'expires_at': ..., 'label': ...}, ...]
    만료 임박 순 정렬, expires_at=None(무기한)은 마지막.
    """
    user_id, operator_id = _resolve_owner(owner)
    supabase = current_app.supabase
    if not supabase or not user_id:
        return []
    try:
        now = now_kst().isoformat()
        q = supabase.table('point_ledger').select('type, remaining, expires_at, note')
        q = _scope_filter(q, operator_id, user_id)
        result = q.gt('remaining', 0).execute()
        rows = result.data or []

        # 만료 안 된 행만
        active = [r for r in rows if not r.get('expires_at') or r['expires_at'] > now]

        TYPE_LABEL = {
            'welcome': '웰컴 포인트',
            'subscription_grant': '구독 포인트',
            'purchase': '구매 포인트',
            'refund': '환급 포인트',
        }

        summary = []
        for r in active:
            summary.append({
                'type': r.get('type', ''),
                'amount': r.get('remaining', 0),
                'expires_at': r.get('expires_at'),
                'label': TYPE_LABEL.get(r.get('type', ''), r.get('note', '')),
            })

        summary.sort(key=lambda x: (x['expires_at'] is None, x['expires_at'] or ''))
        return summary
    except Exception as e:
        logger.error(f'[POINT] get_expiry_summary error: {e}')
        return []


# ─────────────────────────────────────────────────────────────
# 차감 / 충전
# ─────────────────────────────────────────────────────────────

def use_points(owner: Any, creation_type: str, ref_id: str,
               cost_override: int | None = None,
               note_override: str | None = None) -> int:
    """포인트 차감 — 잔액 반환.

    차감 시 만료 임박 버킷부터 소비(remaining 감소)하여
    추후 만료 시 실제 잔여분만 expire 처리되도록 함.
    """
    cost = cost_override if cost_override is not None else POINT_COSTS.get(creation_type)
    if cost is None:
        raise ValueError(f'Unknown creation_type: {creation_type}')

    user_id, operator_id = _resolve_owner(owner)
    if not user_id:
        raise ValueError('use_points: owner 가 비어있습니다.')

    supabase = current_app.supabase
    balance = get_balance(owner)  # 만료 처리 포함
    if balance < cost:
        raise InsufficientPoints(f'잔액 부족 (현재: {balance}P, 필요: {cost}P)')

    new_balance = balance - cost
    row = {
        'user_id': user_id,
        'type': 'use',
        'amount': -cost,
        'balance': new_balance,
        'ref_id': ref_id,
        'note': note_override or CREATION_LABELS.get(creation_type, creation_type),
        'created_at': now_kst().isoformat(),
        'remaining': None,
        'expires_at': None,
    }
    if operator_id:
        row['operator_id'] = operator_id
    supabase.table('point_ledger').insert(row).execute()

    # 만료 임박 버킷부터 remaining 감소 (만료 정산 정확도 향상)
    _consume_from_buckets(user_id, operator_id, cost, supabase)

    return new_balance


def add_points(owner: Any, amount: int, type_: str,
               ref_id: str = '', note: str = '',
               expires_at: str | None = None) -> int:
    """포인트 충전/지급.

    Args:
        expires_at: ISO 형식 만료 시각. None 이면 무기한.
                    웰컴=30일, 구독=period_end, 구매=365일 권장.
    """
    user_id, operator_id = _resolve_owner(owner)
    if not user_id:
        raise ValueError('add_points: owner 가 비어있습니다.')

    supabase = current_app.supabase
    balance = get_balance(owner)
    new_balance = balance + amount
    row = {
        'user_id': user_id,
        'type': type_,            # 'subscription_grant' | 'purchase' | 'refund' | 'welcome'
        'amount': amount,
        'balance': new_balance,
        'ref_id': ref_id,
        'note': note,
        'created_at': now_kst().isoformat(),
        'expires_at': expires_at,
        'remaining': amount if amount > 0 else None,
    }
    if operator_id:
        row['operator_id'] = operator_id
    supabase.table('point_ledger').insert(row).execute()
    return new_balance


# ─────────────────────────────────────────────────────────────
# 구독 포인트 지급 (팀 풀로)
# ─────────────────────────────────────────────────────────────

def grant_monthly_subscription_points(owner: Any, plan_type: str,
                                      expires_at: str | None = None) -> int:
    """구독 월 포인트 지급 — 팀 모드면 팀 풀로.

    Args:
        expires_at: 구독 기간 종료일 (ISO). None 이면 30일 후 자동 설정.
    """
    from models import PLAN_FEATURES
    from datetime import timedelta
    monthly = PLAN_FEATURES.get(plan_type, {}).get('monthly_points', 0)
    if monthly <= 0:
        return get_balance(owner)

    if expires_at is None:
        expires_at = (now_kst() + timedelta(days=30)).isoformat()

    return add_points(
        owner, monthly, 'subscription_grant',
        note=f'{PLAN_FEATURES[plan_type]["label"]} 구독 포인트 지급',
        expires_at=expires_at,
    )
