"""블로그 썸네일 생성 Celery 태스크 모음.

blueprints/create/blog.py의 무거운 썸네일 라우트(classic FLUX 경로/AI 정밀 누끼/
캐릭터 변형/AI 씬)를 워커로 이전. services/async_generation.py 표준 패턴 사용.

디자인 카드형(/blog/thumbnail/design)은 순수 PIL 합성(외부 API 호출 없음, 1초 미만)이라
워커로 옮기면 오히려 폴링 지연만 늘어 제외 — 기존 그대로 동기 유지.
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


@celery.task(bind=True, name='tasks.blog_thumbnail_task.classic_thumbnail',
             max_retries=0, soft_time_limit=180, time_limit=240)
def classic_thumbnail(self, creation_id, user_id, line1, line2, brand_name, accent_color,
                      use_quotes, bg_topic, blog_text, image_prompts,
                      text_y_pct, font_size_pct, overlay_darkness, text_align,
                      line1_color, letter_spacing, text_bg_color, text_bg_opacity,
                      supabase_url, supabase_key, anthropic_api_key):
    """클래식 썸네일 — Claude로 FLUX 배경 프롬프트 생성 → FLUX 배경 → PIL 텍스트 합성.
    (will_generate_flux=True 인 경우에만 이 태스크가 제출됨 — 무료 경로는 라우트에서 동기 처리)
    """
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)
    try:
        from services.claude_service import generate_text as _gen_text
        from services.imagen_service import _generate_flux, generate_blog_thumbnail, upload_to_supabase

        context_parts = []
        if bg_topic:
            context_parts.append(f'[유저 배경 키워드 — 최우선 지시]\n{bg_topic}')
        if image_prompts:
            context_parts.append(
                f'[이미 생성된 블로그 이미지 프롬프트 (영문 FLUX 형식)]\n{image_prompts}')
        if blog_text:
            context_parts.append(f'[블로그 글 내용 발췌 — 보조 참고]\n{blog_text[:600]}')
        context_str = '\n\n'.join(context_parts) if context_parts else '배경 키워드 없음'

        _THUMB_BG_SYSTEM = (
            'You write a FLUX image generation prompt for a blog thumbnail background.\n'
            '\n'
            'PRIORITY ORDER (follow strictly):\n'
            '1. [유저 배경 키워드] = USER\'S DIRECT INSTRUCTION — must be honored exactly.\n'
            '   If user says "창고" → generate warehouse. No exceptions.\n'
            '2. [이미지 프롬프트] = reference for visual style and mood.\n'
            '3. [블로그 글 내용] = supplementary context only.\n'
            '\n'
            'If [유저 배경 키워드] is empty, infer the best background from '
            'the image prompts and blog content.\n'
            '\n'
            'OUTPUT FORMAT: 50-70 word English FLUX prompt only.\n'
            '- Describe exact physical objects, materials, lighting angles\n'
            '- End with: "photorealistic DSLR photography, dark exposure, '
            'no people, no text in image"\n'
            '- NO: futuristic, sci-fi, cyberpunk, neon, glowing panels, '
            'city streets, night market, rain, charts, graphs, screens\n'
            '- Korean input → translate to English first\n'
            '- Output the prompt only, no explanation, no quotes.'
        )
        try:
            bg_prompt = _gen_text(_THUMB_BG_SYSTEM, context_str, max_tokens=220,
                                  model='claude-sonnet-4-6')
            bg_prompt = bg_prompt.strip().strip('"\'').strip()
        except Exception as e:
            logger.warning('[blog_thumb_task] 배경 프롬프트 생성 실패 → 폴백 사용: %s', e)
            bg_prompt = (
                'dark atmospheric interior space with dramatic directional lighting, '
                'photorealistic DSLR photography, dark exposure, '
                'no people, no text in image'
            )

        bg_url = None
        try:
            bg_url, _ = _generate_flux(bg_prompt, 'flux_standard', '1080x1080')
        except Exception as e:
            logger.warning('[blog_thumb_task] FLUX 배경 실패 → PIL 폴백: %s', e)

        img_bytes = generate_blog_thumbnail(
            line1=line1, line2=line2, background_url=bg_url, brand_name=brand_name,
            accent_color=accent_color, line1_color=line1_color, use_quotes=use_quotes,
            text_y_pct=text_y_pct, font_size_pct=font_size_pct,
            overlay_darkness=overlay_darkness, text_align=text_align,
            letter_spacing=letter_spacing, text_bg_color=text_bg_color,
            text_bg_opacity=text_bg_opacity,
        )
        import base64
        b64 = f"data:image/png;base64,{base64.b64encode(img_bytes).decode()}"
        import time as _time
        try:
            public_url = upload_to_supabase(b64, user_id, f'blog_thumbnail_{int(_time.time())}.png',
                                            supabase=supabase)
        except Exception:
            public_url = b64

        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'url': public_url, 'bg_url': bg_url or ''},
        }).eq('id', creation_id).execute()
        logger.info('[blog_thumb_task] classic 완료 cid=%s bg=%s', creation_id,
                   (bg_url or '')[:60])
    except Exception as e:
        logger.error('[blog_thumb_task] classic 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, '블로그 썸네일 생성 실패 — 자동 환불')


@celery.task(bind=True, name='tasks.blog_thumbnail_task.cutout',
             max_retries=0, soft_time_limit=90, time_limit=120)
def cutout(self, creation_id, user_id, character_data, supabase_url, supabase_key):
    """캐릭터 AI 정밀 누끼(birefnet) — 결과를 Storage에 올리고 URL을 output_data에 저장.
    (라우트/상태 응답에서 data URL로 재조립해 프론트 계약 유지)"""
    _setup()
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    try:
        import requests
        from io import BytesIO
        from PIL import Image
        from services.imagen_service import remove_background_ai, upload_to_supabase
        from services.thumbnail_studio import fill_alpha_holes

        result_url = remove_background_ai(character_data, user_id or 'anon',
                                          supabase=supabase)
        r = requests.get(result_url, timeout=30)
        r.raise_for_status()
        im = fill_alpha_holes(Image.open(BytesIO(r.content)))
        buf = BytesIO()
        im.save(buf, format='PNG')
        stable_url = upload_to_supabase(
            f"data:image/png;base64,{__import__('base64').b64encode(buf.getvalue()).decode()}",
            user_id, f'cutout_{creation_id[:8]}.png', supabase=supabase)

        supabase.table('creations').update({
            'status': 'done', 'output_data': {'result_url': stable_url},
        }).eq('id', creation_id).execute()
        logger.info('[blog_thumb_task] cutout 완료 cid=%s', creation_id)
    except Exception as e:
        logger.error('[blog_thumb_task] cutout 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, '캐릭터 AI 누끼 실패 — 자동 환불')


@celery.task(bind=True, name='tasks.blog_thumbnail_task.transform_character',
             max_retries=0, soft_time_limit=150, time_limit=180)
def transform_character(self, creation_id, user_id, character_data, style,
                        supabase_url, supabase_key):
    """캐릭터 변형(nano-banana) — 결과를 무료 누끼 후 Storage에 올리고 URL 저장."""
    _setup()
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    try:
        import requests, base64
        from io import BytesIO
        from PIL import Image
        from services.imagen_service import transform_character as transform_character_ai, upload_to_supabase
        from services.thumbnail_studio import auto_cutout

        result_url = transform_character_ai(character_data, style, user_id or 'anon',
                                            supabase=supabase)
        r = requests.get(result_url, timeout=30)
        r.raise_for_status()
        cut = auto_cutout(Image.open(BytesIO(r.content)))
        buf = BytesIO()
        cut.save(buf, format='PNG')
        stable_url = upload_to_supabase(
            f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}",
            user_id, f'transform_{creation_id[:8]}.png', supabase=supabase)

        supabase.table('creations').update({
            'status': 'done', 'output_data': {'result_url': stable_url},
        }).eq('id', creation_id).execute()
        logger.info('[blog_thumb_task] transform 완료 cid=%s style=%s', creation_id, style[:30])
    except Exception as e:
        logger.error('[blog_thumb_task] transform 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, '캐릭터 AI 변형 실패 — 자동 환불')


@celery.task(bind=True, name='tasks.blog_thumbnail_task.scene',
             max_retries=0, soft_time_limit=180, time_limit=240)
def scene(self, creation_id, user_id, headline, sub, badge, cta, theme, title_style,
         topic, refs, supabase_url, supabase_key, anthropic_api_key,
         style='cute_char'):
    """AI 씬 썸네일 — 선택한 그림체로 장면 생성 후 상단 텍스트 하이브리드 합성.

    style 기본값은 기존 동작('캐릭터 아기자기') — 인플라이트 태스크 호환용.
    """
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)
    try:
        import requests, base64, time as _time
        from services.imagen_service import generate_scene, upload_to_supabase
        from services.thumbnail_studio import render_thumbnail

        _THEME_BG = {
            'baby_blue':   'soft pastel baby blue',
            'food_cream':  'warm cream and soft orange',
            'fresh_green': 'fresh pastel mint green',
            'warm_pink':   'soft warm pink',
        }
        bg_color = _THEME_BG.get(theme, 'soft pastel')

        scene_url = generate_scene(refs, topic, user_id or 'anon',
                                   bg_color=bg_color, style=style, supabase=supabase)

        r = requests.get(scene_url, timeout=60)
        r.raise_for_status()
        img_bytes = render_thumbnail(
            headline=headline, sub=sub, badge=badge, cta=cta, theme=theme,
            bg_image=r.content, title_style=title_style,
        )
        b64 = f"data:image/png;base64,{base64.b64encode(img_bytes).decode()}"
        try:
            url = upload_to_supabase(b64, user_id, f'blog_thumb_scene_{int(_time.time())}.png',
                                     supabase=supabase)
        except Exception:
            url = b64

        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'url': url, 'style': 'scene', 'scene_style': style},
        }).eq('id', creation_id).execute()
        logger.info('[blog_thumb_task] scene 완료 cid=%s theme=%s style=%s topic=%s char=%s',
                   creation_id, theme, style, topic[:30], 'Y' if refs else 'N')
    except Exception as e:
        logger.error('[blog_thumb_task] scene 오류 cid=%s: %s', creation_id, e, exc_info=True)
        _fail(supabase, creation_id, e, user_id, 'AI 씬 썸네일 실패 — 자동 환불')
