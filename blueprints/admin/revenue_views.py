"""어드민 — 매출/결제 관리

월/분기/연 매출 합계 · 부가세 분리 · 환불 차감 · CSV 다운로드 · 환불 처리.
"""
import csv
import io
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from flask import render_template, request, current_app, jsonify, Response
from flask_login import current_user

from blueprints.admin import admin_bp
from models import require_superadmin

logger = logging.getLogger(__name__)
_KST = ZoneInfo('Asia/Seoul')


# ──────────────────────────────────────
# 유틸
# ──────────────────────────────────────

def _month_range(year: int, month: int) -> tuple[str, str]:
    start = datetime(year, month, 1, tzinfo=_KST)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=_KST)
    else:
        end = datetime(year, month + 1, 1, tzinfo=_KST)
    return start.isoformat(), end.isoformat()


def _quarter_range(year: int, quarter: int) -> tuple[str, str]:
    sm = (quarter - 1) * 3 + 1
    em = sm + 3
    start = datetime(year, sm, 1, tzinfo=_KST)
    end   = datetime(year + 1, em - 12, 1, tzinfo=_KST) if em > 12 else datetime(year, em, 1, tzinfo=_KST)
    return start.isoformat(), end.isoformat()


def _fetch_payments(supabase, start_iso: str, end_iso: str) -> list:
    full_cols = ('id,user_id,operator_id,payment_id,amount,supply_amount,tax_amount,'
                 'status,refund_status,refund_amount,pg_provider,'
                 'order_name,payment_type,paid_at,refunded_at,updated_at')
    minimal_cols = 'id,user_id,payment_id,amount,status,refund_status,refund_amount,payment_type,paid_at'

    for cols in (full_cols, minimal_cols):
        try:
            res = supabase.table('payments') \
                .select(cols) \
                .gte('paid_at', start_iso) \
                .lt('paid_at', end_iso) \
                .limit(10000) \
                .execute()
            return res.data or []
        except Exception as e:
            logger.warning(f'[Admin/Revenue] payments 조회 실패 (cols={cols[:40]}...): {e}')
            if cols == minimal_cols:
                return []
            # 풀 컬럼 실패 → 최소 컬럼으로 재시도
            logger.warning('[Admin/Revenue] 최소 컬럼으로 폴백 재시도')
    return []


def _aggregate(rows: list) -> dict:
    total = supply = tax = refund = 0
    by_pg = {'card': 0, 'kakaopay': 0, 'other': 0}
    paid_count = refund_count = 0

    for r in rows or []:
        amt = int(r.get('amount') or 0)
        if r.get('status') == 'paid':
            total      += amt
            supply     += int(r.get('supply_amount') or 0)
            tax        += int(r.get('tax_amount') or 0)
            paid_count += 1
            pg = (r.get('pg_provider') or '').lower()
            if 'kakao' in pg or pg == 'kakaopay':
                by_pg['kakaopay'] += amt
            elif ('card' in pg or 'kpn' in pg or 'tosspayments' in pg
                  or pg in ('', 'unknown')):
                by_pg['card'] += amt
            else:
                by_pg['other'] += amt
        if r.get('refund_status') == 'completed':
            refund        += int(r.get('refund_amount') or 0)
            refund_count  += 1

    return {
        'total':        total,
        'supply':       supply,
        'tax':          tax,
        'refund':       refund,
        'net':          total - refund,
        'by_pg':        by_pg,
        'paid_count':   paid_count,
        'refund_count': refund_count,
    }


# ──────────────────────────────────────
# MRR 계산 (활성 구독 기준)
# ──────────────────────────────────────

def _calc_mrr(supabase) -> int:
    from models import PLAN_FEATURES
    try:
        res = supabase.table('subscriptions') \
            .select('plan_type') \
            .eq('status', 'active') \
            .eq('auto_renewal', True) \
            .execute()
        total = 0
        for row in res.data or []:
            price = int(PLAN_FEATURES.get(row['plan_type'], {}).get('price', 0) or 0)
            total += price
        return total
    except Exception as e:
        logger.warning(f'[Admin/Revenue] MRR 계산 실패: {e}')
        return 0


# ──────────────────────────────────────
# 메인 매출 페이지
# ──────────────────────────────────────

@admin_bp.route('/revenue')
@require_superadmin
def revenue():
    now_kst = datetime.now(_KST)
    year  = int(request.args.get('year',  now_kst.year))
    month = int(request.args.get('month', now_kst.month))
    view  = request.args.get('view', 'month')

    sb = current_app.supabase

    if view == 'year':
        start = datetime(year, 1, 1, tzinfo=_KST).isoformat()
        end   = datetime(year + 1, 1, 1, tzinfo=_KST).isoformat()
        label = f'{year}년'
    elif view == 'quarter':
        q = (month - 1) // 3 + 1
        start, end = _quarter_range(year, q)
        label = f'{year}년 {q}분기'
    else:
        start, end = _month_range(year, month)
        label = f'{year}년 {month}월'

    rows    = _fetch_payments(sb, start, end)
    summary = _aggregate(rows)
    mrr     = _calc_mrr(sb)

    # 최근 12개월 트렌드 (차트용)
    trend = []
    for i in range(11, -1, -1):
        d  = now_kst.replace(day=1) - timedelta(days=30 * i)
        ms, me = _month_range(d.year, d.month)
        tr = _fetch_payments(sb, ms, me)
        ag = _aggregate(tr)
        trend.append({
            'ym':    f'{d.year}-{d.month:02d}',
            'net':   ag['net'],
            'total': ag['total'],
        })

    # 환불 대기 건 (refund_status = 'requested')
    refund_queue = []
    try:
        rq = sb.table('payments') \
            .select('id,payment_id,user_id,operator_id,amount,order_name,refund_reason,'
                    'refund_requested_at,paid_at') \
            .eq('refund_status', 'requested') \
            .order('refund_requested_at', desc=True) \
            .limit(50) \
            .execute()
        refund_queue = rq.data or []
    except Exception as e:
        logger.warning(f'[Admin/Revenue] 환불대기 조회 실패: {e}')

    return render_template('admin/revenue.html',
        view=view, year=year, month=month, label=label,
        summary=summary, mrr=mrr,
        trend=trend,
        refund_queue=refund_queue,
        now_year=now_kst.year,
    )


# ──────────────────────────────────────
# 결제 내역 상세
# ──────────────────────────────────────

@admin_bp.route('/revenue/payments')
@require_superadmin
def revenue_payments():
    sb      = current_app.supabase
    now_kst = datetime.now(_KST)
    year    = int(request.args.get('year',  now_kst.year))
    month   = int(request.args.get('month', now_kst.month))
    status  = request.args.get('status', '')
    ptype   = request.args.get('type', '')
    start, end = _month_range(year, month)

    refund_only = request.args.get('refund_only') == '1'

    # DB에 확실히 존재하는 기본 컬럼만 SELECT
    base_cols = 'id,user_id,payment_id,amount,status,refund_status,refund_amount,payment_type,paid_at'
    # 추가 컬럼은 별도로 시도 (없으면 기본만 사용)
    extra_cols = 'supply_amount,tax_amount,pg_provider,order_name,refund_reason,refund_requested_at,refunded_at'
    rows = []
    for cols in (f'{base_cols},{extra_cols}', base_cols):
        try:
            q = sb.table('payments').select(cols)
            if refund_only:
                q = q.eq('refund_status', 'requested') \
                     .order('refund_requested_at', desc=True).limit(500)
            else:
                q = q.gte('paid_at', start).lt('paid_at', end)
                if status:
                    q = q.eq('status', status)
                if ptype:
                    q = q.eq('payment_type', ptype)
                q = q.order('paid_at', desc=True).limit(2000)
            rows = q.execute().data or []
            break
        except Exception as e:
            logger.warning(f'[Admin/Revenue] 내역 조회 실패 ({cols[:30]}...): {e}')
            rows = []

    # 템플릿이 기대하는 키 정규화 (없는 컬럼 → None/0 기본값)
    _row_defaults = {
        'supply_amount': 0, 'tax_amount': 0, 'refund_amount': 0,
        'pg_provider': None, 'order_name': None,
        'refund_reason': None, 'refund_requested_at': None,
        'refunded_at': None, 'refund_status': None, 'payment_id': None,
    }
    for r in rows:
        for k, v in _row_defaults.items():
            r.setdefault(k, v)

    return render_template('admin/revenue_payments.html',
        rows=rows, year=year, month=month, status=status, ptype=ptype,
        refund_only=refund_only,
    )


# ──────────────────────────────────────
# 어드민 환불 처리
# ──────────────────────────────────────

@admin_bp.route('/revenue/refund', methods=['POST'])
@require_superadmin
def revenue_refund():
    """어드민 직접 환불. POST JSON: {payment_id, amount(선택), reason}"""
    from services.payment_service import cancel_payment

    sb   = current_app.supabase
    data = request.get_json(force=True) or {}

    payment_id = (data.get('payment_id') or '').strip()
    reason     = (data.get('reason') or '관리자 환불').strip()[:200]
    amount_raw = data.get('amount')
    amount     = int(amount_raw) if amount_raw else None

    if not payment_id:
        return jsonify(success=False, error='payment_id 누락'), 400

    result = cancel_payment(payment_id=payment_id, reason=reason, amount=amount)
    if not result.get('success'):
        return jsonify(success=False, error=result.get('error', '취소 실패')), 400

    cancelled_amount  = result.get('cancelled_amount', amount or 0)
    cancellation_id   = result.get('cancellation_id', '')
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        sb.table('payments').update({
            'refund_status':        'completed',
            'refund_amount':        cancelled_amount,
            'refund_reason':        reason,
            'refund_payment_id':    cancellation_id,
            'refund_requested_at':  now_iso,
            'refunded_at':          now_iso,
            'updated_at':           now_iso,
        }).eq('payment_id', payment_id).execute()
    except Exception as e:
        logger.error(f'[Admin/Refund] DB 업데이트 실패 pid={payment_id}: {e}')
        return jsonify(
            success=True,
            warning='환불은 성공했으나 DB 업데이트 실패. 수동 확인 필요.',
            cancelled_amount=cancelled_amount,
        )

    logger.info(
        f'[Admin/Refund] 환불 완료 pid={payment_id} amt={cancelled_amount} '
        f'by={current_user.email}'
    )
    return jsonify(success=True, cancelled_amount=cancelled_amount, cancellation_id=cancellation_id)


# ──────────────────────────────────────
# CSV 다운로드
# ──────────────────────────────────────

@admin_bp.route('/revenue/export.csv')
@require_superadmin
def revenue_export_csv():
    now_kst = datetime.now(_KST)
    year  = int(request.args.get('year',  now_kst.year))
    month = int(request.args.get('month', now_kst.month))
    start, end = _month_range(year, month)

    sb = current_app.supabase
    try:
        res = sb.table('payments') \
            .select('payment_id,user_id,operator_id,amount,supply_amount,tax_amount,'
                    'refund_amount,refund_status,status,pg_provider,'
                    'order_name,payment_type,paid_at,refunded_at') \
            .gte('paid_at', start).lt('paid_at', end) \
            .order('paid_at', desc=True) \
            .limit(10000) \
            .execute()
        rows = res.data or []
    except Exception as e:
        logger.error(f'[Admin/Revenue] CSV 조회 실패: {e}')
        rows = []

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        '거래일자', '주문명', 'payment_id', '유저ID', '운영사ID',
        '결제금액', '공급가액', '부가세', '환불금액', '실수령액',
        'PG', '결제상태', '환불상태', '결제유형',
    ])
    for r in rows:
        amt     = int(r.get('amount') or 0)
        refund  = int(r.get('refund_amount') or 0)
        supply  = int(r.get('supply_amount') or 0)
        tax     = int(r.get('tax_amount') or 0)
        paid_at = (r.get('paid_at') or '')[:19].replace('T', ' ')
        w.writerow([
            paid_at,
            r.get('order_name', ''),
            r.get('payment_id', ''),
            r.get('user_id', ''),
            r.get('operator_id', ''),
            amt, supply, tax, refund, amt - refund,
            r.get('pg_provider', ''),
            r.get('status', ''),
            r.get('refund_status', ''),
            r.get('payment_type', ''),
        ])

    filename = f'payments_{year}{month:02d}.csv'
    return Response(
        '﻿' + out.getvalue(),   # BOM for 엑셀 한글
        mimetype='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )
