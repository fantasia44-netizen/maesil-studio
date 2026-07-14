"""제안서/인쇄물(카탈로그·리플릿·전단지) 생성 Celery 태스크.

_promo_base.py의 run_promo_generation()이 기존에 run_text_generation()으로
동기 처리하던 Claude 텍스트 호출(최대 8000 max_tokens, 32p 카탈로그 등)을
메인 서버 블로킹 없이 워커로 이전.
"""
import logging

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, name='tasks.promo_task.generate_promo_text',
             max_retries=0, soft_time_limit=120, time_limit=150)
def generate_promo_text(self, creation_id, user_id, system_prompt, user_prompt,
                        max_tokens, model,
                        supabase_url, supabase_key, anthropic_api_key):
    """제안서/인쇄물 Claude 텍스트 생성."""
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)

    try:
        from services.claude_service import generate_text
        output_text = generate_text(system_prompt, user_prompt,
                                    max_tokens=max_tokens, model=model)

        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'text': output_text},
        }).eq('id', creation_id).execute()
        logger.info('[promo_task] 생성 완료 cid=%s', creation_id)

    except Exception as e:
        logger.error('[promo_task] 오류 cid=%s: %s', creation_id, e, exc_info=True)
        from services.async_generation import mark_task_failed
        mark_task_failed(supabase, creation_id, e,
                         '제안서/인쇄물 생성 실패 — 자동 환불', user_id)
        raise
