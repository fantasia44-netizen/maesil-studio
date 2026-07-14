"""인스타그램 이미지 생성 Celery 태스크 — blueprints/create/instagram.py의
instagram_image_generate() 동기 로직 이전. services/async_generation.py 표준 패턴 사용.
"""
import logging

from celery_app import celery

logger = logging.getLogger(__name__)


def _setup():
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)


@celery.task(bind=True, name='tasks.instagram_task.generate_image',
             max_retries=0, soft_time_limit=180, time_limit=240)
def generate_image(self, creation_id, user_id, style, flux_prompt, flux_size_str, pil_size,
                   brand_color, title, body_text, dialogue1, dialogue2, text_color,
                   overlay_strength, supabase_url, supabase_key, anthropic_api_key):
    """스타일별 FLUX/Ideogram + PIL 합성 (웹툰/실사배너/일러스트 공통)."""
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)
    try:
        from services.imagen_service import _generate_flux, upload_to_supabase
        from services.instagram_service import create_banner_image, create_webtoon_image

        translated_prompt = ''
        bg_url = None

        if style == 'webtoon':
            bg_url, translated_prompt = _generate_flux(flux_prompt, 'flux_preview', flux_size_str)
            dialogues = [d for d in [dialogue1, dialogue2] if d]
            data_url  = create_webtoon_image(bg_url, dialogues, pil_size)
            final_url = upload_to_supabase(data_url, user_id, f'insta_webtoon_{creation_id[:8]}.jpg',
                                           supabase=supabase)
        else:
            img_url, translated_prompt = _generate_flux(flux_prompt, 'flux_preview', flux_size_str)
            bg_url = img_url
            texts = [t for t in [title, body_text] if t]
            if texts:
                data_url  = create_banner_image(
                    img_url, texts, brand_color, pil_size,
                    text_color=text_color, overlay_strength=overlay_strength,
                )
                final_url = upload_to_supabase(data_url, user_id, f'insta_{style}_{creation_id[:8]}.jpg',
                                               supabase=supabase)
            else:
                final_url = upload_to_supabase(img_url, user_id, f'insta_{style}_{creation_id[:8]}.jpg',
                                               supabase=supabase)

        supabase.table('creations').update({
            'output_data': {'image_url': final_url, 'base_image_url': bg_url,
                           'translated_prompt': translated_prompt or None},
            'status': 'done',
        }).eq('id', creation_id).execute()
        logger.info('[instagram_task] 완료 cid=%s style=%s', creation_id, style)
    except Exception as e:
        logger.error('[instagram_task] 오류 cid=%s: %s', creation_id, e, exc_info=True)
        from services.async_generation import mark_task_failed
        mark_task_failed(supabase, creation_id, e,
                         refund_note='인스타그램 이미지 생성 실패 — 자동 환불', user_id=user_id)
        raise
