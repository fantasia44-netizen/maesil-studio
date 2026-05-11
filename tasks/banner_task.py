"""배너 이미지 생성 Celery 태스크"""
import logging

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
    """Celery 워커에서 배너 이미지 생성."""
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

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


@celery.task(bind=True, name='tasks.banner_task.generate_product_banner', max_retries=0)
def generate_product_banner(
    self,
    creation_id: str,
    user_id: str,
    headline: str,
    subline: str,
    cta: str,
    bg_preset: str,
    bg_prompt_custom: str,
    brand_color: str,
    layout: str,
    W: int,
    H: int,
    product_url: str,
    supabase_url: str,
    supabase_key: str,
):
    """Celery 워커에서 상품 배너 생성 (Bria 배경 교체 + PIL)."""
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    from services.banner_service import run_product_banner_pipeline

    supabase = create_client(supabase_url, supabase_key)
    run_product_banner_pipeline(
        creation_id=creation_id,
        user_id=user_id,
        headline=headline,
        subline=subline,
        cta=cta,
        bg_preset=bg_preset,
        bg_prompt_custom=bg_prompt_custom,
        brand_color=brand_color,
        layout=layout,
        W=W, H=H,
        product_url=product_url,
        supabase=supabase,
    )
