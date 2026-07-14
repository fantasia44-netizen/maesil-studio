"""상세페이지 빌더 이미지 생성 Celery 태스크 모음.

blueprints/create/detail_page_builder.py의 5개 동기 이미지 생성 라우트
(gen-image/bg-replace/flux-text/feature3/story-section)를 워커로 이전.
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


def _fail(supabase, creation_id, e, user_id, note):
    from services.async_generation import mark_task_failed
    mark_task_failed(supabase, creation_id, e, refund_note=note, user_id=user_id)
    raise e


@celery.task(bind=True, name='tasks.detail_page_builder_task.gen_image',
             max_retries=0, soft_time_limit=180, time_limit=240)
def gen_image(self, creation_id, user_id, image_prompt, engine, block_role,
             supabase_url, supabase_key, anthropic_api_key):
    """블록 이미지 FLUX 생성."""
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)
    try:
        from services.imagen_service import generate_image, upload_to_supabase
        image_url, prompt_en = generate_image(image_prompt, engine=engine, size='1024x1024')
        stable_url = upload_to_supabase(image_url, user_id, f'dpb_{block_role}.jpg',
                                        supabase=supabase)
        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'image_url': stable_url, 'prompt_used': prompt_en},
        }).eq('id', creation_id).execute()
    except Exception as e:
        logger.error('[dpb_task] gen_image 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, '상세페이지 이미지 생성 실패 — 자동 환불')


@celery.task(bind=True, name='tasks.detail_page_builder_task.bg_replace',
             max_retries=0, soft_time_limit=180, time_limit=240)
def bg_replace(self, creation_id, user_id, image_url, bg_prompt,
              supabase_url, supabase_key, anthropic_api_key):
    """제품 이미지 배경 교체 (Bria)."""
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)
    try:
        from services.imagen_service import (
            replace_background, upload_to_supabase, _translate_prompt, _has_korean,
        )
        bg_en = _translate_prompt(bg_prompt) if _has_korean(bg_prompt) else bg_prompt
        new_url = replace_background(image_url, bg_en)
        stable_url = upload_to_supabase(new_url, user_id, f'dpb_bg_{creation_id[:6]}.jpg',
                                        supabase=supabase)
        supabase.table('creations').update({
            'status': 'done', 'output_data': {'image_url': stable_url},
        }).eq('id', creation_id).execute()
    except Exception as e:
        logger.error('[dpb_task] bg_replace 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, '상세페이지 배경 교체 실패 — 자동 환불')


@celery.task(bind=True, name='tasks.detail_page_builder_task.flux_text',
             max_retries=0, soft_time_limit=180, time_limit=240)
def flux_text(self, creation_id, user_id, texts, bg_prompt, brand_color, font_color,
             supabase_url, supabase_key, anthropic_api_key):
    """FLUX 배경 + PIL 한글 텍스트 합성."""
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)
    try:
        from services.imagen_service import generate_card_news, upload_to_supabase
        data_url, prompt_used = generate_card_news(
            texts=texts[:3], background_prompt=bg_prompt,
            brand_color=brand_color, font_color=font_color)
        stable_url = upload_to_supabase(data_url, user_id, f'dpb_txt_{creation_id[:6]}.jpg',
                                        supabase=supabase)
        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'image_url': stable_url, 'prompt_used': prompt_used},
        }).eq('id', creation_id).execute()
    except Exception as e:
        logger.error('[dpb_task] flux_text 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, '상세페이지 텍스트 조합 이미지 실패 — 자동 환불')


@celery.task(bind=True, name='tasks.detail_page_builder_task.feature3',
             max_retries=0, soft_time_limit=120, time_limit=180)
def feature3(self, creation_id, user_id, bg_image_url, headline, features, brand_color,
            supabase_url, supabase_key, anthropic_api_key=''):
    """소구포인트 3열 섹션 PNG (PIL 합성만, 외부 API 호출 없음)."""
    _setup()
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    try:
        import base64
        from services.imagen_service import generate_feature3_section, upload_to_supabase
        png_bytes = generate_feature3_section(
            bg_image_url=bg_image_url, headline=headline,
            features=features[:3], brand_color=brand_color)
        b64 = base64.b64encode(png_bytes).decode()
        data_url = f'data:image/png;base64,{b64}'
        stable_url = upload_to_supabase(data_url, user_id, f'dpb_feat3_{creation_id[:8]}.png',
                                        supabase=supabase)
        supabase.table('creations').update({
            'status': 'done', 'output_data': {'image_url': stable_url},
        }).eq('id', creation_id).execute()
    except Exception as e:
        logger.error('[dpb_task] feature3 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, '상세페이지 소구포인트 이미지 실패 — 자동 환불')


@celery.task(bind=True, name='tasks.detail_page_builder_task.story_section',
             max_retries=0, soft_time_limit=180, time_limit=240)
def story_section(self, creation_id, user_id, tmpl, section, brand_color,
                  supabase_url, supabase_key, anthropic_api_key):
    """상세페이지 스토리 세트 — 섹션 1개 이미지 생성 (템플릿별 분기)."""
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)
    try:
        from services.imagen_service import (
            generate_hero_section, generate_feature3_section,
            generate_feature_highlight, generate_text_emphasis,
            generate_cta_section, upload_to_supabase, generate_image,
        )
        import base64

        bg_url = (section.get('_bg_override') or '').strip()
        if not bg_url and tmpl != 'text_emphasis' and section.get('bg_prompt'):
            try:
                bg_url, _ = generate_image(section['bg_prompt'], engine='flux_preview',
                                           size='1024x768')
            except Exception as flux_err:
                logger.warning('[dpb_task] story bg-image 실패, 단색 배경 사용: %s', flux_err)

        if tmpl == 'hero':
            png = generate_hero_section(
                bg_url, section.get('headline', ''), section.get('subtext', ''), brand_color)
        elif tmpl == 'feature3':
            png = generate_feature3_section(
                bg_url, section.get('headline', ''), section.get('features', []), brand_color)
        elif tmpl == 'feature_highlight':
            png = generate_feature_highlight(
                bg_url, section.get('number', '01'), section.get('title', ''),
                section.get('desc', ''), brand_color, section.get('layout', 'left'))
        elif tmpl == 'text_emphasis':
            png = generate_text_emphasis(
                section.get('main_text', ''), section.get('sub_text', ''), brand_color)
        elif tmpl == 'cta':
            png = generate_cta_section(
                bg_url, section.get('cta_text', ''), section.get('sub_text', ''), brand_color)
        else:
            raise ValueError(f'알 수 없는 템플릿: {tmpl}')

        b64 = base64.b64encode(png).decode()
        data_url = f'data:image/png;base64,{b64}'
        stable_url = upload_to_supabase(data_url, user_id, f'dpb_{tmpl}_{creation_id[:8]}.png',
                                        supabase=supabase)
        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'image_url': stable_url, 'template': tmpl},
        }).eq('id', creation_id).execute()
    except Exception as e:
        logger.error('[dpb_task] story_section 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, '상세페이지 스토리 섹션 실패 — 자동 환불')
