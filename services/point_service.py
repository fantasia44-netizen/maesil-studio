"""포인트 잔액 조회 / 차감 / 충전 — 팀(Operator) 풀 지원.

핵심 규칙:
  - user.operator_id 가 있으면  → operator 풀 (팀 공유)
  - user.operator_id 가 없으면 → user 풀 (개인 계정 종전 동작)

행 저장 시 user_id 는 항상 채움(누가 사용/생성했는지 감사). operator 모드는
operator_id 도 함께 채움.

호환성:
  기존 API (use_points/add_points/get_balance/get_ledger) 의 첫 인자는 user_id
  문자열이지만, 이제 User 객체도 받는다. 객체면 operator_id 자동 추론.
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
# 잔액 / 이력
# ─────────────────────────────────────────────────────────────

def get_balance(owner: Any) -> int:
    """현재 잔액 — point_ledger 최신 balance 컬럼 즉시 조회."""
    user_id, operator_id = _resolve_owner(owner)
    supabase = current_app.supabase
    if not supabase or not user_id:
        return 0
    try:
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


# ─────────────────────────────────────────────────────────────
# 차감 / 충전
# ─────────────────────────────────────────────────────────────

def use_points(owner: Any, creation_type: str, ref_id: str,
               cost_override: int | None = None,
               note_override: str | None = None) -> int:
    """포인트 차감 — 잔액 반환.

    cost_override: POINT_COSTS 기본값 대신 사용 (예: 분량별 블로그 비용).
    note_override: ledger 메모 직접 지정.
    """
    cost = cost_override if cost_override is not None else POINT_COSTS.get(creation_type)
    if cost is None:
        raise ValueError(f'Unknown creation_type: {creation_type}')

    user_id, operator_id = _resolve_owner(owner)
    if not user_id:
        raise ValueError('use_points: owner 가 비어있습니다.')

    supabase = current_app.supabase
    balance = get_balance(owner)
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
    }
    if operator_id:
        row['operator_id'] = operator_id
    supabase.table('point_ledger').insert(row).execute()
    return new_balance


def add_points(owner: Any, amount: int, type_: str,
               ref_id: str = '', note: str = '') -> int:
    """포인트 충전/지급."""
    user_id, operator_id = _resolve_owner(owner)
    if not user_id:
        raise ValueError('add_points: owner 가 비어있습니다.')

    supabase = current_app.supabase
    balance = get_balance(owner)
    new_balance = balance + amount
    row = {
        'user_id': user_id,
        'type': type_,            # 'subscription_grant' | 'purchase' | 'refund'
        'amount': amount,
        'balance': new_balance,
        'ref_id': ref_id,
        'note': note,
        'created_at': now_kst().isoformat(),
    }
    if operator_id:
        row['operator_id'] = operator_id
    supabase.table('point_ledger').insert(row).execute()
    return new_balance


# ─────────────────────────────────────────────────────────────
# 구독 포인트 지급 (팀 풀로)
# ─────────────────────────────────────────────────────────────

def grant_monthly_subscription_points(owner: Any, plan_type: str) -> int:
    """구독 월 포인트 지급 — 팀 모드면 팀 풀로."""
    from models import PLAN_FEATURES
    monthly = PLAN_FEATURES.get(plan_type, {}).get('monthly_points', 0)
    if monthly <= 0:
        return get_balance(owner)
    return add_points(
        owner, monthly, 'subscription_grant',
        note=f'{PLAN_FEATURES[plan_type]["label"]} 구독 포인트 지급',
    )
