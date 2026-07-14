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


@celery.task(bind=True, name='tasks.shorts_task.generate_preview_image',
             max_retries=0, soft_time_limit=90, time_limit=120)
def generate_preview_image(self, creation_id, user_id, flux_prompt, style,
                          supabase_url, supabase_key):
    """씬 1개 FLUX 미리보기 이미지 생성 — /shorts/preview-image가 씬마다 순차 호출.
    무료 미리보기 단계라 포인트 환불 없음(과금 자체가 없음)."""
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    try:
        from services.shorts_service import SHORTS_STYLE_PRESETS, _NO_CJK, _NO_ANATOMY
        from services.imagen_service import _generate_flux

        style_mod   = SHORTS_STYLE_PRESETS.get(style, '')
        full_prompt = (
            flux_prompt +
            (f', {style_mod}' if style_mod else '') +
            ', 9:16 vertical frame, cinematic lighting' +
            _NO_CJK + _NO_ANATOMY
        )
        img_url, _ = _generate_flux(full_prompt, 'flux_preview', '1080x1920')

        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'image_url': img_url, 'prompt_used': flux_prompt},
        }).eq('id', creation_id).execute()
        logger.info('[shorts_task] preview_image 완료 cid=%s', creation_id)
    except Exception as e:
        logger.error('[shorts_task] preview_image 오류 cid=%s: %s', creation_id, e, exc_info=True)
        try:
            supabase.table('creations').update({
                'status': 'failed', 'output_data': {'error': str(e)[:200]},
            }).eq('id', creation_id).execute()
        except Exception:
            pass
        raise


@celery.task(bind=True, name='tasks.shorts_task.generate_scene_images',
             max_retries=0, soft_time_limit=180, time_limit=240)
def generate_scene_images(self, creation_id, user_id, scenes, style,
                          supabase_url, supabase_key):
    """5씬 FLUX 이미지 일괄 생성(순차) — 메인 서버 블로킹 없이 워커에서 처리.
    무료 미리보기 단계라 포인트 환불 없음(과금 자체가 없음)."""
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    try:
        from services.shorts_service import SHORTS_STYLE_PRESETS, _NO_CJK, _NO_ANATOMY
        from services.imagen_service import _generate_flux
        from services.kling_service import ensure_english_prompt

        style_mod = SHORTS_STYLE_PRESETS.get(style, '')
        results = []
        for i, scene in enumerate(scenes):
            try:
                flux_prompt = ensure_english_prompt(scene.get('flux_prompt', '') or scene.get('narration', ''))
                full_prompt = (
                    flux_prompt +
                    (f', {style_mod}' if style_mod else '') +
                    ', 9:16 vertical frame, cinematic lighting' +
                    _NO_CJK + _NO_ANATOMY
                )
                img_url, _ = _generate_flux(full_prompt, 'flux_standard', '1080x1920')
                results.append({'idx': i, 'image_url': img_url, 'ok': True})
            except Exception as e:
                logger.error('[shorts_task] 씬%d 이미지 생성 실패: %s', i, e)
                results.append({'idx': i, 'image_url': None, 'ok': False, 'error': str(e)[:100]})

        supabase.table('creations').update({
            'status': 'done', 'output_data': {'images': results},
        }).eq('id', creation_id).execute()
        logger.info('[shorts_task] scene_images 완료 cid=%s scenes=%d', creation_id, len(scenes))
    except Exception as e:
        logger.error('[shorts_task] scene_images 오류 cid=%s: %s', creation_id, e, exc_info=True)
        try:
            supabase.table('creations').update({
                'status': 'failed', 'output_data': {'error': str(e)[:200]},
            }).eq('id', creation_id).execute()
        except Exception:
            pass
        raise


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
    scene_images: list | None = None,
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
            scene_images=scene_images,
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
    soft_time_limit=1500,   # 25분 (3씬 순차 체이닝 × 씬당 최대 5분 + 여유)
    time_limit=1620,        # 27분 hard kill
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
    product_image_url: str | None = None,
    ref_image_url: str | None = None,
    scene_images: list | None = None,
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
            product_image_url=product_image_url,
            ref_image_url=ref_image_url,
            scene_images=scene_images,
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
