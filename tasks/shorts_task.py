"""쇼츠 영상 생성 Celery 태스크"""
import logging
import os

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, name='tasks.shorts_task.generate_shorts_video', max_retries=0)
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
    """Celery 워커에서 쇼츠 영상 생성.

    Supabase 클라이언트는 워커에서 재생성 (직렬화 불가 → URL/Key 전달).
    """
    from supabase import create_client
    from services.shorts_service import run_shorts_pipeline

    supabase = create_client(supabase_url, supabase_key)

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
