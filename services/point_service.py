"""포인트 잔액 조회 / 차감 / 충전"""
import logging
from flask import current_app
from models import POINT_COSTS, CREATION_LABELS
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)


class InsufficientPoints(Exception):
    pass


def get_balance(user_id: str) -> int:
    """point_ledger 최신 balance 컬럼으로 즉시 조회"""
    supabase = current_app.supabase
    if not supabase:
        return 0
    try:
        row = (supabase.table('point_ledger')
               .select('balance')
               .eq('user_id', user_id)
               .order('created_at', desc=True)
               .limit(1)
               .execute())
        return (row.data[0].get('balance', 0)) if row.data else 0
    except Exception as e:
        logger.error(f'[POINT] get_balance error: {e}')
        return 0


def use_points(user_id: str, creation_type: str, ref_id: str,
               cost_override: int | None = None,
               note_override: str | None = None) -> int:
    """포인트 차감 — 잔액 반환.

    cost_override: POINT_COSTS 기본값 대신 사용 (예: 분량별 블로그 비용).
    note_override: ledger 메모 문구 직접 지정 (예: '블로그 (1,000자)').
    """
    cost = cost_override if cost_override is not None else POINT_COSTS.get(creation_type)
    if cost is None:
        raise ValueError(f'Unknown creation_type: {creation_type}')

    supabase = current_app.supabase
    balance = get_balance(user_id)
    if balance < cost:
        raise InsufficientPoints(f'잔액 부족 (현재: {balance}P, 필요: {cost}P)')

    new_balance = balance - cost
    supabase.table('point_ledger').insert({
        'user_id': user_id,
        'type': 'use',
        'amount': -cost,
        'balance': new_balance,
        'ref_id': ref_id,
        'note': note_override or CREATION_LABELS.get(creation_type, creation_type),
        'created_at': now_kst().isoformat(),
    }).execute()
    return new_balance


def add_points(user_id: str, amount: int, type_: str, ref_id: str = '', note: str = '') -> int:
    """포인트 충전/지급"""
    supabase = current_app.supabase
    balance = get_balance(user_id)
    new_balance = balance + amount
    supabase.table('point_ledger').insert({
        'user_id': user_id,
        'type': type_,  # 'subscription_grant' | 'purchase' | 'refund'
        'amount': amount,
        'balance': new_balance,
        'ref_id': ref_id,
        'note': note,
        'created_at': now_kst().isoformat(),
    }).execute()
    return new_balance


def get_ledger(user_id: str, limit: int = 50) -> list:
    """포인트 입출내역 조회"""
    supabase = current_app.supabase
    if not supabase:
        return []
    try:
        result = (supabase.table('point_ledger')
                  .select('*')
                  .eq('user_id', user_id)
                  .order('created_at', desc=True)
                  .limit(limit)
                  .execute())
        return result.data or []
    except Exception as e:
        logger.error(f'[POINT] get_ledger error: {e}')
        return []


def grant_monthly_subscription_points(user_id: str, plan_type: str) -> int:
    """구독 월 포인트 지급"""
    from models import PLAN_FEATURES
    monthly = PLAN_FEATURES.get(plan_type, {}).get('monthly_points', 0)
    if monthly <= 0:
        return get_balance(user_id)
    return add_points(
        user_id, monthly, 'subscription_grant',
        note=f'{PLAN_FEATURES[plan_type]["label"]} 구독 포인트 지급',
    )
