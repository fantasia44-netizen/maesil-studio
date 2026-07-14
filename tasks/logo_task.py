"""로고 생성 Celery 태스크"""
import logging

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, name='tasks.logo_task.generate_logo', max_retries=0)
def generate_logo(
    self,
    creation_id: str,
    user_id: str,
    brand_name: str,
    brand_name_ko: str,
    tagline: str,
    logo_style: str,
    vibe: str,
    primary_color: str,
    extra: str,
    supabase_url: str,
    supabase_key: str,
):
    """Celery 워커에서 로고 3개 생성 (Ideogram V3)."""
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    from blueprints.create.logo import run_logo_pipeline

    supabase = create_client(supabase_url, supabase_key)
    run_logo_pipeline(
        creation_id=creation_id,
        brand_name=brand_name,
        brand_name_ko=brand_name_ko,
        tagline=tagline,
        logo_style=logo_style,
        vibe=vibe,
        primary_color=primary_color,
        extra=extra,
        supabase=supabase,
        user_id=user_id,
    )
