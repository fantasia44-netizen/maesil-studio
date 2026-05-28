"""구독 자동갱신 스케줄러 (APScheduler)

매일 02:00 KST — next_billing_at <= now 이고 auto_renewal=true 인 구독을
PortOne charge_subscription 으로 청구.

성공: payments upsert + subscriptions 기간 갱신 + failed_attempt_count=0
실패: failed_attempt_count++ → 1~2회 past_due(+3일 재시도), 3회 → cancelled + is_active=False
"""
import logging
import os

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_scheduler = None
_KST = ZoneInfo('Asia/Seoul')


def init_scheduler(app):
    """APScheduler 초기화 — app.py create_app() 마지막에서 호출."""
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.warning('[Scheduler] 이미 실행 중 — 중복 init 무시')
        return

    # Werkzeug 개발 서버 이중 실행 방지
    if app.debug and not os.environ.get('WERKZEUG_RUN_MAIN'):
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.jobstores.memory import MemoryJobStore

        _scheduler = BackgroundScheduler(
            jobstores={'default': MemoryJobStore()},
            job_defaults={'coalesce': True, 'max_instances': 1},
            timezone=_KST,
        )

        # 정기결제 자동갱신 — 매일 02:00 KST
        _scheduler.add_job(
            _run_subscription_renewal,
            'cron', hour=2, minute=0,
            id='subscription_renewal',
            kwargs={'app': app},
        )

        # 트라이얼 만료 체크 — 매일 09:00 KST
        _scheduler.add_job(
            _check_trial_expiry,
            'cron', hour=9, minute=0,
            id='trial_expiry_check',
            kwargs={'app': app},
        )

        _scheduler.start()
        logger.info('[Scheduler] APScheduler 시작 — 구독갱신(02:00) + 트라이얼만료(09:00)')

    except ImportError:
        logger.warning('[Scheduler] APScheduler 미설치 — 백그라운드 작업 비활성화')
    except Exception as e:
        logger.error(f'[Scheduler] APScheduler 초기화 실패: {e}')


def shutdown_scheduler():
    global _scheduler
    if _scheduler is None:
        return
    try:
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
    except Exception as e:
        logger.error(f'[Scheduler] shutdown 실패: {e}')
    finally:
        _scheduler = None


# ──────────────────────────────────────
# 정기결제 자동갱신
# ──────────────────────────────────────

def _run_subscription_renewal(app):
    """매일 02:00 KST — auto_renewal 구독 결제 처리."""
    with app.app_context():
        try:
            supabase = app.supabase
            if not supabase:
                return

            from datetime import datetime, timezone, timedelta
            from services.payment_service import charge_subscription, cancel_payment
            from models import PLAN_FEATURES

            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()

            # 결제 대상 조회
            try:
                subs_res = supabase.table('subscriptions') \
                    .select('id,user_id,operator_id,plan_type,billing_key,'
                            'failed_attempt_count,auto_renewal,status') \
                    .eq('auto_renewal', True) \
                    .in_('status', ['active', 'past_due']) \
                    .lte('next_billing_at', now_iso) \
                    .limit(500) \
                    .execute()
                subs_rows = subs_res.data or []
            except Exception as e:
                logger.error(f'[Renewal] 대상 조회 실패: {e}')
                return

            if not subs_rows:
                logger.info('[Renewal] 결제 대상 없음')
                return

            logger.info(f'[Renewal] 대상 {len(subs_rows)}건 처리 시작')
            success_count = failed_count = locked_count = 0

            for sub in subs_rows:
                user_id     = sub.get('user_id')
                operator_id = sub.get('operator_id')
                plan_type   = sub.get('plan_type') or 'starter'
                cur_attempt = int(sub.get('failed_attempt_count') or 0)
                billing_key = sub.get('billing_key')

                # 빌링키가 subscription row 에 없으면 user/operator 에서 가져옴
                owner_id  = operator_id or user_id
                owner_tbl = 'operators' if operator_id else 'users'

                if not billing_key:
                    try:
                        owner_res = supabase.table(owner_tbl) \
                            .select('billing_key,billing_key_pg,email,name') \
                            .eq('id', owner_id).single().execute()
                        owner = owner_res.data or {}
                        billing_key = owner.get('billing_key')
                    except Exception as e:
                        logger.warning(f'[Renewal] owner 조회 실패 {owner_id}: {e}')
                        continue
                else:
                    try:
                        owner_res = supabase.table(owner_tbl) \
                            .select('billing_key_pg,email,name') \
                            .eq('id', owner_id).single().execute()
                        owner = owner_res.data or {}
                    except Exception:
                        owner = {}

                if not billing_key:
                    logger.info(f'[Renewal] billing_key 없음 — skip owner={owner_id}')
                    continue

                plan_info = PLAN_FEATURES.get(plan_type, {})
                amount = int(plan_info.get('price', 0) or 0)
                if amount <= 0:
                    continue

                pg = (owner.get('billing_key_pg') or 'card').lower()
                plan_label = plan_info.get('label', plan_type)
                order_name = f'매실 스튜디오 {plan_label} 플랜 정기결제'
                customer   = {
                    'customerId': owner_id,
                    'fullName':   owner.get('name') or '',
                    'email':      owner.get('email') or '',
                }

                logger.info(f'[Renewal] 시도 owner={owner_id} plan={plan_type} amt={amount} attempt={cur_attempt+1}')

                try:
                    result = charge_subscription(
                        owner_id=owner_id,
                        billing_key=billing_key,
                        amount=amount,
                        order_name=order_name,
                        pg=pg,
                        customer=customer,
                        id_prefix='auto',
                    )
                except Exception as e:
                    logger.error(f'[Renewal] charge 예외 owner={owner_id}: {e}')
                    result = {'success': False, 'error': str(e)}

                if result.get('success'):
                    _handle_renewal_success(
                        supabase, sub, owner_id, owner_tbl,
                        result, amount, plan_type, order_name, now_iso,
                    )
                    success_count += 1
                else:
                    did_lock = _handle_renewal_failure(
                        supabase, sub, owner_id, owner_tbl,
                        result, cur_attempt, amount, order_name, now_iso, now,
                        owner,
                    )
                    failed_count += 1
                    if did_lock:
                        locked_count += 1

            logger.info(
                f'[Renewal] 완료 — 성공:{success_count} 실패:{failed_count} 잠금:{locked_count}'
            )
        except Exception as e:
            logger.error(f'[Renewal] 전체 예외: {e}')


def _handle_renewal_success(supabase, sub, owner_id, owner_tbl,
                             result, amount, plan_type, order_name, now_iso):
    from datetime import datetime, timezone, timedelta
    from services.payment_service import cancel_payment

    payment_id = result.get('payment_id')
    period_end = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    tax = round(amount / 11.0)
    supply = amount - tax

    try:
        supabase.table('payments').upsert({
            'payment_id':    payment_id,
            'user_id':       sub.get('user_id'),
            'operator_id':   sub.get('operator_id'),
            'amount':        amount,
            'supply_amount': supply,
            'tax_amount':    tax,
            'order_name':    order_name,
            'payment_type':  'auto_renewal',
            'status':        'paid',
            'raw_data':      result.get('data'),
            'paid_at':       now_iso,
            'updated_at':    now_iso,
        }, on_conflict='payment_id').execute()

        # 이전 상태 확인 (past_due → active 복구 시 is_active 재활성화 필요)
        prev_status = sub.get('status', '')

        supabase.table('subscriptions').update({
            'status':               'active',
            'current_period_start': now_iso,
            'current_period_end':   period_end,
            'next_billing_at':      period_end,
            'failed_attempt_count': 0,
            'last_retry_at':        None,
            'billing_key':          sub.get('billing_key'),
            'updated_at':           now_iso,
        }).eq('id', sub['id']).execute()

        if prev_status in ('past_due', 'cancelled'):
            supabase.table(owner_tbl).update({'is_active': True}).eq('id', owner_id).execute()

        # 구독 갱신 포인트 지급
        try:
            from services.point_service import grant_monthly_subscription_points

            class _FakeOwner:
                def __init__(self, uid, oid):
                    self.id = uid or oid
                    self.operator_id = oid

            fake_owner = _FakeOwner(sub.get('user_id'), sub.get('operator_id'))
            grant_monthly_subscription_points(fake_owner, plan_type, expires_at=period_end)
        except Exception as pts_err:
            logger.warning(f'[Renewal] 포인트 지급 실패 owner={owner_id}: {pts_err}')

        logger.info(f'[Renewal] 성공 owner={owner_id} pid={payment_id}')

    except Exception as db_err:
        logger.error(f'[Renewal] 성공 후 DB 반영 실패 owner={owner_id}: {db_err}')
        # 보상 트랜잭션: 결제 성공 + DB 실패 → 자동환불
        try:
            cancel_result = cancel_payment(
                payment_id=payment_id,
                reason=f'정기결제 DB 반영 실패 자동환불 ({str(db_err)[:80]})',
            )
            if cancel_result.get('success'):
                logger.warning(f'[Renewal] DB 실패 → 자동환불 성공 owner={owner_id}')
            else:
                logger.error(
                    f'[Renewal] DB 실패 + 자동환불 실패 owner={owner_id} '
                    f'err={cancel_result.get("error")}'
                )
        except Exception as cancel_err:
            logger.error(f'[Renewal] 자동환불 호출 예외 owner={owner_id}: {cancel_err}')


def _handle_renewal_failure(supabase, sub, owner_id, owner_tbl,
                             result, cur_attempt, amount, order_name,
                             now_iso, now, owner) -> bool:
    """실패 처리. 잠금 발생 시 True 반환."""
    from datetime import timedelta

    err_msg     = (result.get('error') or '')[:200]
    new_attempt = cur_attempt + 1
    next_retry  = (now + timedelta(days=3)).isoformat()
    payment_id  = (
        result.get('payment_id')
        or f'failed_{owner_id[:8]}_{int(now.timestamp())}'
    )

    try:
        supabase.table('payments').upsert({
            'payment_id':   payment_id,
            'user_id':      sub.get('user_id'),
            'operator_id':  sub.get('operator_id'),
            'amount':       0,
            'order_name':   order_name,
            'payment_type': 'auto_renewal',
            'status':       'failed',
            'raw_data':     {'error': err_msg, 'attempt': new_attempt},
            'updated_at':   now_iso,
        }, on_conflict='payment_id').execute()
    except Exception as e:
        logger.error(f'[Renewal] 실패 결제 로그 실패 owner={owner_id}: {e}')

    locked = False
    if new_attempt >= 3:
        # 3회 이상 실패 → cancelled + is_active=False
        try:
            supabase.table('subscriptions').update({
                'status':               'cancelled',
                'auto_renewal':         False,
                'failed_attempt_count': new_attempt,
                'last_retry_at':        now_iso,
                'updated_at':           now_iso,
            }).eq('id', sub['id']).execute()
            supabase.table(owner_tbl).update({'is_active': False}).eq('id', owner_id).execute()
            locked = True
            logger.warning(f'[Renewal] 3회 실패 — 서비스 잠금 owner={owner_id}')
        except Exception as e:
            logger.error(f'[Renewal] 잠금 처리 실패 owner={owner_id}: {e}')
    else:
        # 1~2회 실패 → past_due + 3일 후 재시도
        try:
            supabase.table('subscriptions').update({
                'status':               'past_due',
                'failed_attempt_count': new_attempt,
                'last_retry_at':        now_iso,
                'next_billing_at':      next_retry,
                'updated_at':           now_iso,
            }).eq('id', sub['id']).execute()
            logger.info(f'[Renewal] {new_attempt}회 실패 → past_due 재시도 owner={owner_id}')
        except Exception as e:
            logger.error(f'[Renewal] past_due 업데이트 실패 owner={owner_id}: {e}')

    return locked


# ──────────────────────────────────────
# 트라이얼 만료 체크
# ──────────────────────────────────────

def _check_trial_expiry(app):
    """매일 09:00 KST — trial 구독 중 current_period_end 지난 건을 expired 처리."""
    with app.app_context():
        try:
            supabase = app.supabase
            if not supabase:
                return

            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()

            expired_res = supabase.table('subscriptions') \
                .select('id,user_id,operator_id') \
                .eq('status', 'trial') \
                .lt('current_period_end', now_iso) \
                .limit(500) \
                .execute()
            rows = expired_res.data or []

            if not rows:
                return

            for row in rows:
                try:
                    supabase.table('subscriptions').update({
                        'status': 'expired',
                        'updated_at': now_iso,
                    }).eq('id', row['id']).execute()

                    # plan_type → free 로 다운그레이드
                    uid = row.get('user_id')
                    if uid:
                        supabase.table('users').update({
                            'plan_type': 'free',
                        }).eq('id', uid).execute()
                except Exception as e:
                    logger.error(f'[TrialExpiry] 처리 실패 sub_id={row["id"]}: {e}')

            logger.info(f'[TrialExpiry] {len(rows)}건 만료 처리 완료')
        except Exception as e:
            logger.error(f'[TrialExpiry] 전체 예외: {e}')
