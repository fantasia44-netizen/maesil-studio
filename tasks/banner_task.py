"""배너 이미지 생성 Celery 태스크"""
import logging
import os
import sys

# ForkPoolWorker 자식 프로세스에서도 프로젝트 루트가 sys.path에 있도록 보장
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # tasks/ -> 프로젝트 루트
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, name='tasks.banner_task.generate_banner', max_retries=0)
def generate_banner(
    self,
    creation_id: str,
    user_id: str,
    headline: str,
    subline: str,
    cta: str,
    bg_type: str,
    bg_prompt: str,
    brand_color: str,
    layout: str,
    W: int,
    H: int,
    product_url,
    supabase_url: str,
    supabase_key: str,
):
    """Celery 워커에서 배너 이미지 생성.

    Supabase 클라이언트는 워커에서 재생성 (직렬화 불가 → URL/Key 전달).
    """
    from supabase import create_client
    from services.banner_service import run_banner_pipeline

    supabase = create_client(supabase_url, supabase_key)

    run_banner_pipeline(
        creation_id=creation_id,
        user_id=user_id,
        headline=headline,
        subline=subline,
        cta=cta,
        bg_type=bg_type,
        bg_prompt=bg_prompt,
        brand_color=brand_color,
        layout=layout,
        W=W,
        H=H,
        product_url=product_url,
        supabase=supabase,
    )
