"""어드민 - 생성 작업 정리 (멈춘 generating → failed + 포인트 환불)"""
import logging
from datetime import datetime, timezone, timedelta
from flask import jsonify, request, render_template, current_app
from flask_login import login_required
from blueprints.admin import admin_bp
from models import require_superadmin

logger = logging.getLogger(__name__)

# 이 시간(분) 이상 generating 상태면 stale로 간주
DEFAULT_STALE_MINUTES = 30


@admin_bp.route('/cleanup')
@login_required
@require_superadmin
def cleanup():
    """Stale 생성 작업 현황 조회 페이지."""
    supabase = current_app.supabase
    stale_minutes = request.args.get('minutes', DEFAULT_STALE_MINUTES, type=int)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()

    stale = []
    try:
        rows = (
            supabase.table('creations')
            .select('id, creation_type, user_id, operator_id, points_used, created_at, output_data')
            .eq('status', 'generating')
            .lt('created_at', cutoff)
            .order('created_at', desc=True)
            .limit(100)
            .execute()
        )
        stale = rows.data or []
    except Exception as e:
        logger.error('[cleanup] stale 조회 오류: %s', e)

    return render_template(
        'admin/cleanup.html',
        stale=stale,
        stale_minutes=stale_minutes,
        cutoff=cutoff,
    )


@admin_bp.route('/cleanup/run', methods=['POST'])
@login_required
@require_superadmin
def cleanup_run():
    """Stale generating 레코드를 failed로 전환 + 포인트 환불."""
    supabase = current_app.supabase
    data = request.json or {}
    stale_minutes = int(data.get('minutes', DEFAULT_STALE_MINUTES))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()

    # 명시적 ID 목록이 있으면 그것만, 없으면 cutoff 기준 전체
    ids = data.get('ids', [])

    try:
        if ids:
            rows_r = (
                supabase.table('creations')
                .select('id, user_id, operator_id, points_used')
                .in_('id', ids)
                .eq('status', 'generating')
                .execute()
            )
        else:
            rows_r = (
                supabase.table('creations')
                .select('id, user_id, operator_id, points_used')
                .eq('status', 'generating')
                .lt('created_at', cutoff)
                .execute()
            )
        rows = rows_r.data or []
    except Exception as e:
        return jsonify(ok=False, message=f'조회 오류: {e}')

    if not rows:
        return jsonify(ok=True, message='정리할 레코드 없음', count=0, refunded=0)

    success_count = 0
    refund_total  = 0
    errors        = []

    for row in rows:
        cid      = row['id']
        user_id  = row.get('user_id') or ''
        op_id    = row.get('operator_id')
        pts      = int(row.get('points_used') or 0)

        try:
            # 1) status → failed
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {'error': '생성 중 서버 오류로 자동 실패 처리 (관리자)'},
            }).eq('id', cid).execute()

            # 2) 포인트 환불
            if pts > 0 and user_id:
                _refund(supabase, user_id, op_id, cid, pts)
                refund_total += pts

            success_count += 1
        except Exception as e:
            logger.error('[cleanup] 처리 오류 (%s): %s', cid, e)
            errors.append(f'{cid}: {e}')

    msg = f'{success_count}개 처리 완료, {refund_total}P 환불'
    if errors:
        msg += f' | 오류 {len(errors)}건'

    logger.info('[cleanup] %s', msg)
    return jsonify(ok=True, message=msg, count=success_count, refunded=refund_total, errors=errors)


def _refund(supabase, user_id: str, operator_id, creation_id: str, pts: int):
    """포인트 환불 처리 — point_ledger에 잔액 누적 행 INSERT.

    point_balances 테이블은 없음. 잔액은 point_ledger 최신 balance 컬럼으로 관리.
    """
    # 현재 잔액 조회 (최신 ledger 행의 balance)
    if operator_id:
        bal_row = (
            supabase.table('point_ledger')
            .select('balance')
            .eq('operator_id', operator_id)
            .order('created_at', desc=True)
            .limit(1)
            .execute()
        )
    else:
        bal_row = (
            supabase.table('point_ledger')
            .select('balance')
            .eq('user_id', user_id)
            .is_('operator_id', 'null')
            .order('created_at', desc=True)
            .limit(1)
            .execute()
        )
    current = (bal_row.data[0].get('balance', 0)) if bal_row and bal_row.data else 0
    new_bal = current + pts

    row = {
        'user_id': user_id,
        'type': 'refund',
        'amount': pts,
        'balance': new_bal,
        'ref_id': creation_id,
        'note': '쇼츠 영상 생성 실패 — 관리자 수동 정리',
    }
    if operator_id:
        row['operator_id'] = operator_id
    supabase.table('point_ledger').insert(row).execute()
