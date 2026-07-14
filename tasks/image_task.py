"""범용 이미지 생성 Celery 태스크 — blueprints/create/image.py의 image_generate() 동기 로직 이전.

FLUX/Ideogram/카드뉴스/배경교체 생성은 외부 API 왕복(수 초~수십 초)이 걸려 메인
서버(gunicorn 동기 워커)를 블로킹하고 메모리를 잡아먹으므로 워커로 분리.
services/async_generation.py의 표준 패턴(submit_async_generation/mark_task_failed) 사용.
"""
import logging

from celery_app import celery

logger = logging.getLogger(__name__)


def _setup():
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)


@celery.task(bind=True, name='tasks.image_task.generate_image_task',
             max_retries=0, soft_time_limit=180, time_limit=240)
def generate_image_task(self, creation_id, user_id, engine, prompt, size,
                        style_preset, brand_color, texts, reference_image_url,
                        supabase_url, supabase_key, anthropic_api_key):
    """(카드뉴스/배경교체/일반) 이미지 생성 → Storage 업로드 → creations 갱신."""
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)

    try:
        from services.imagen_service import (
            generate_image, generate_card_news, replace_background, upload_to_supabase,
        )

        translated_prompt = ''
        if engine == 'card_news':
            image_url, translated_prompt = generate_card_news(
                texts or [prompt], prompt, brand_color)
        elif engine == 'bg_replace':
            if not reference_image_url:
                raise ValueError('배경 교체는 원본 이미지 URL이 필요합니다.')
            image_url = replace_background(reference_image_url, prompt)
        else:
            image_url, translated_prompt = generate_image(
                prompt, engine, style_preset, size, brand_color)

        filename = f'{engine}_{creation_id[:8]}.jpg'
        public_url = upload_to_supabase(image_url, user_id, filename, supabase=supabase)

        supabase.table('creations').update({
            'output_data': {'image_url': public_url,
                            'translated_prompt': translated_prompt or None},
            'status': 'done',
        }).eq('id', creation_id).execute()

        logger.info('[image_task] 완료 cid=%s engine=%s', creation_id, engine)

    except Exception as e:
        logger.error('[image_task] 오류 cid=%s: %s', creation_id, e, exc_info=True)
        from services.async_generation import mark_task_failed
        mark_task_failed(supabase, creation_id, e,
                         refund_note='이미지 생성 실패 — 자동 환불', user_id=user_id)
        raise
