"""생성 작업 → Celery 워커 제출 공통 헬퍼.

experience_task.py/experience.py에서 확립된 패턴(포인트 차감 → creations(generating)
insert → task.delay() 제출 → 프론트는 폴링)을 여러 기능에서 반복 구현하지 않도록 추출.

표준 흐름:
  라우트: submit_async_generation(...) 호출 → {ok, id, async_mode:true} 반환
  태스크: 작업 성공 시 creations.status='done' 갱신
          실패 시 mark_task_failed(...) 한 줄로 실패 기록 + 포인트 환불
  상태 라우트: render_status_response(...)로 done/failed/generating 응답 조립
"""
import os
import logging
from typing import Any, Callable

from flask import current_app, jsonify

logger = logging.getLogger(__name__)


class AsyncSubmitError(Exception):
    """제출 단계 실패(주로 포인트 부족) — 라우트에서 잡아 jsonify(ok=False, message=str(e))."""


def submit_async_generation(*, owner, creation_type: str, cost: int, input_data: dict,
                            task_delay_fn: Callable, task_kwargs: dict,
                            model_used: str | None = None,
                            extra_row: dict | None = None) -> str:
    """포인트 차감 + creations(status='generating') insert + Celery 태스크 제출.

    task_kwargs에는 creation_id/user_id/supabase_url/supabase_key를 넣지 않는다 —
    이 함수가 자동으로 주입한다(태스크는 Flask 컨텍스트가 없는 별도 프로세스이므로
    Supabase 접속 정보를 환경변수에서 재도출해 넘겨야 함).
    extra_row: creations insert 행에 추가로 merge할 필드(예: brand_id).

    반환: creation_id. 잔액 부족 시 AsyncSubmitError(message) 발생.
    """
    from services.point_service import get_balance, use_points, InsufficientPoints

    balance = get_balance(owner)
    if balance < cost:
        raise AsyncSubmitError(f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    import uuid as _uuid
    from services.tz_utils import now_kst
    creation_id = str(_uuid.uuid4())

    supabase = current_app.supabase
    if supabase:
        try:
            now_s = now_kst().isoformat()
            row = {
                'id': creation_id, 'user_id': owner.id,
                'creation_type': creation_type,
                'input_data': input_data, 'output_data': {},
                'points_used': cost, 'status': 'generating',
                'created_at': now_s, 'updated_at': now_s,
            }
            if model_used:
                row['model_used'] = model_used
            if getattr(owner, 'operator_id', None):
                row['operator_id'] = owner.operator_id
            if extra_row:
                row.update(extra_row)
            supabase.table('creations').insert(row).execute()
        except Exception as e:
            logger.warning('[async_generation] creations insert 실패: %s', e)

    try:
        use_points(owner, creation_type, creation_id, cost_override=cost)
    except InsufficientPoints as e:
        if supabase:
            try:
                supabase.table('creations').update({'status': 'failed'}).eq(
                    'id', creation_id).execute()
            except Exception:
                pass
        raise AsyncSubmitError(str(e))

    from services.config_service import get_config
    task_kwargs = dict(task_kwargs)
    task_kwargs.update(
        creation_id=creation_id,
        user_id=owner.id,
        supabase_url=os.environ.get('SUPABASE_URL', ''),
        supabase_key=(os.environ.get('SUPABASE_SERVICE_KEY')
                      or os.environ.get('SUPABASE_KEY', '')),
    )
    # 대부분의 태스크가 Anthropic 키도 필요로 하므로 이미 kwargs에 없을 때만 채워줌
    task_kwargs.setdefault('anthropic_api_key',
                           get_config('anthropic_api_key') or
                           os.environ.get('ANTHROPIC_API_KEY', ''))
    task_delay_fn(**task_kwargs)

    return creation_id


def mark_task_failed(supabase, creation_id: str, error: Exception,
                     refund_note: str, user_id: str) -> None:
    """태스크 실패 처리 — creations.status='failed' 기록 + 포인트 환불.

    experience_task.py의 _refund_experience_points를 일반화. 절대 예외를 올리지 않음
    (태스크의 except 블록에서 호출 후 그대로 raise 이어가도 안전).
    """
    try:
        supabase.table('creations').update({
            'status': 'failed',
            'output_data': {'error': str(error)[:200]},
        }).eq('id', creation_id).execute()
    except Exception as e:
        logger.error('[async_generation] 실패 상태 기록 오류 cid=%s: %s', creation_id, e)

    try:
        _refund_points(supabase, creation_id, user_id, refund_note)
    except Exception as e:
        logger.error('[async_generation] 포인트 환불 오류 cid=%s: %s', creation_id, e)


def _refund_points(supabase, creation_id: str, user_id: str, note: str) -> None:
    row = supabase.table('creations').select(
        'points_used, operator_id'
    ).eq('id', creation_id).maybe_single().execute()
    if not row or not row.data:
        return
    pts = int(row.data.get('points_used') or 0)
    if pts <= 0:
        return

    operator_id = row.data.get('operator_id')
    if operator_id:
        bal_row = supabase.table('point_ledger').select('balance').eq(
            'operator_id', operator_id
        ).order('created_at', desc=True).limit(1).execute()
    else:
        bal_row = supabase.table('point_ledger').select('balance').eq(
            'user_id', user_id
        ).is_('operator_id', 'null').order('created_at', desc=True).limit(1).execute()

    current = (bal_row.data[0].get('balance', 0)) if bal_row and bal_row.data else 0
    new_bal = current + pts

    ledger_row = {
        'user_id': user_id, 'type': 'refund', 'amount': pts,
        'balance': new_bal, 'ref_id': creation_id, 'note': note,
    }
    if operator_id:
        ledger_row['operator_id'] = operator_id
    supabase.table('point_ledger').insert(ledger_row).execute()
    logger.info('[async_generation] 포인트 환불 완료: %dP → user=%s (잔액 %d)',
                pts, user_id, new_bal)


def render_status_response(row: dict | None, user_id: str, *,
                           done_fields: dict[str, str] | None = None,
                           failed_suffix: str = ''):
    """GET .../status/<id> 공통 응답 조립.

    row: creations 테이블에서 select한 딕셔너리(id, status, output_data, user_id 포함) 또는 None.
    done_fields: {응답키: output_data키} 매핑. 생략 시 output_data를 그대로 펼쳐 반환.
    """
    if not row or row.get('user_id') != user_id:
        return jsonify(ok=False, status='error', message='권한이 없거나 찾을 수 없습니다.')

    status = row.get('status', '')
    od = row.get('output_data') or {}

    if status == 'done':
        payload = {k: od.get(v) for k, v in done_fields.items()} \
            if done_fields else dict(od)
        return jsonify(ok=True, status='done', **payload)
    elif status == 'failed':
        msg = (od.get('error') or '생성 중 오류가 발생했습니다.') + failed_suffix
        return jsonify(ok=False, status='failed', message=msg)
    else:
        return jsonify(ok=True, status='generating')
