"""쇼츠 영상 생성 Celery 태스크"""
import logging
import os

from celery_app import celery

logger = logging.getLogger(__name__)

# 태스크 타임아웃 설정
# - soft_time_limit: 이 시간 초과 시 SoftTimeLimitExceeded 예외 → 정상 종료 처리
# - time_limit: hard kill (강제 종료)
_SOFT_LIMIT = int(os.environ.get('SHORTS_TASK_SOFT_LIMIT', 600))   # 10분
_HARD_LIMIT = int(os.environ.get('SHORTS_TASK_HARD_LIMIT', 660))   # 11분


@celery.task(
    bind=True,
    name='tasks.shorts_task.generate_shorts_video',
    max_retries=0,
    soft_time_limit=_SOFT_LIMIT,
    time_limit=_HARD_LIMIT,
)
def generate_shorts_video(
    self,
    creation_id: str,
    user_id: str,
    scenes: list,
    style: str,
    brand_color: str,
    voice_key: str,
    tts_speed: float,
    supabase_url: str,
    supabase_key: str,
    bgm_volume: float = 0.20,
):
    """Celery 워커에서 쇼츠 영상 생성."""
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    from services.shorts_service import run_shorts_pipeline

    supabase = create_client(supabase_url, supabase_key)

    try:
        run_shorts_pipeline(
            creation_id=creation_id,
            user_id=user_id,
            scenes=scenes,
            style=style,
            brand_color=brand_color,
            voice_key=voice_key,
            tts_speed=tts_speed,
            supabase=supabase,
            bgm_volume=bgm_volume,
        )
    except Exception as exc:
        # SoftTimeLimitExceeded 포함 모든 예외 — DB 실패 처리 + 포인트 환불
        logger.error('[shorts_task] 태스크 실패 (%s): %s', creation_id, exc)
        try:
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {'error': f'태스크 시간초과 또는 오류: {str(exc)[:200]}'},
            }).eq('id', creation_id).execute()
        except Exception as db_e:
            logger.error('[shorts_task] DB 실패 업데이트 오류: %s', db_e)
        # 포인트 환불
        try:
            _refund_shorts_points(supabase, creation_id, user_id)
        except Exception as ref_e:
            logger.error('[shorts_task] 포인트 환불 오류: %s', ref_e)


@celery.task(
    bind=True,
    name='tasks.shorts_task.generate_kling_shorts_video',
    max_retries=0,
    soft_time_limit=1200,   # 20분 (Kling 폴링 시간 고려)
    time_limit=1320,        # 22분 hard kill
)
def generate_kling_shorts_video(
    self,
    creation_id: str,
    user_id: str,
    scenes: list,
    style: str,
    brand_color: str,
    voice_key: str,
    tts_speed: float,
    supabase_url: str,
    supabase_key: str,
    bgm_volume: float = 0.20,
    kling_model: str = 'kling-v1-6',
):
    """Celery 워커 — Kling image2video 기반 쇼츠 영상 생성."""
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    from services.shorts_service import run_kling_shorts_pipeline

    supabase = create_client(supabase_url, supabase_key)

    try:
        run_kling_shorts_pipeline(
            creation_id=creation_id,
            user_id=user_id,
            scenes=scenes,
            style=style,
            brand_color=brand_color,
            voice_key=voice_key,
            tts_speed=tts_speed,
            supabase=supabase,
            bgm_volume=bgm_volume,
            kling_model=kling_model,
        )
    except Exception as exc:
        logger.error('[kling_task] 태스크 실패 (%s): %s', creation_id, exc)
        try:
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {'error': f'Kling 태스크 오류: {str(exc)[:200]}'},
            }).eq('id', creation_id).execute()
        except Exception:
            pass
        try:
            _refund_shorts_points(supabase, creation_id, user_id)
        except Exception:
            pass


def _refund_shorts_points(supabase, creation_id: str, user_id: str) -> None:
    """shorts_video 실패 시 차감 포인트 환불.

    point_ledger 최신 balance에 pts를 더한 새 행 INSERT.
    """
    row = supabase.table('creations').select(
        'points_used, operator_id'
    ).eq('id', creation_id).maybe_single().execute()
    if not row or not row.data:
        return
    pts = int(row.data.get('points_used') or 0)
    if pts <= 0:
        return

    operator_id = row.data.get('operator_id')

    # 현재 잔액 조회 (최신 ledger 행의 balance 컬럼)
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
        'user_id': user_id,
        'type': 'refund',
        'amount': pts,
        'balance': new_bal,
        'ref_id': creation_id,
        'note': '쇼츠 영상 생성 실패 — 자동 환불',
    }
    if operator_id:
        ledger_row['operator_id'] = operator_id
    supabase.table('point_ledger').insert(ledger_row).execute()
    logger.info('[shorts_task] 포인트 환불 완료: %dP → user=%s (잔액 %d)', pts, user_id, new_bal)
