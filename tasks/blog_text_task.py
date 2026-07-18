"""일반 블로그 "구글판 포함" 본문 생성 Celery 태스크 ('구글만' 또는 '네이버+구글 세트').

네이버 단독 생성은 blueprints/create/blog.py에서 기존처럼 동기(run_text_generation)로
처리하고, 이 태스크는 구글판이 섞여 출력이 길어지는 옵션에서만 쓰인다
(tasks/experience_task.py와 동일한 이유 — 메인 서버 블로킹 방지).
"""
import logging

from celery_app import celery
from services.text_split import split_naver_google

logger = logging.getLogger(__name__)


def _setup():
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)


def _apply_disclaimer(text: str, disclaimer: str) -> str:
    """services.regulatory.append_disclaimer 와 동일 로직 — 워커에는 Flask 앱
    컨텍스트가 없어 current_app.supabase 조회가 불가하므로, 라우트에서 미리
    조회한 disclaimer 문자열을 그대로 받아 순수 텍스트 처리만 한다."""
    if not text or not disclaimer:
        return text
    head = disclaimer.splitlines()[0].strip() if disclaimer else ''
    if head and head in text:
        return text
    return f'{text.rstrip()}\n\n---\n\n{disclaimer}'


@celery.task(bind=True, name='tasks.blog_text_task.generate_blog_both',
             max_retries=0, soft_time_limit=240, time_limit=300)
def generate_blog_both(self, creation_id, user_id, system_prompt, user_prompt,
                       max_tokens, supabase_url, supabase_key, anthropic_api_key,
                       disclaimer='', brand_id=None, mode='both'):
    """블로그 본문 — 구글판만, 또는 네이버판 + 구글판을 함께 생성.

    mode='both': [[[NAVER]]]/[[[GOOGLE]]] 구분자로 응답을 분리.
    mode='google': 프롬프트가 애초에 구글판 하나만 요청했으므로 분리 없이
      전체 응답을 그대로 구글판으로 사용(네이버판은 빈 문자열).

    brand_id 가 있고 그 브랜드에 워드프레스가 연결되어 있으면, 구글판 생성 직후
    자동으로 초안(draft)으로 워드프레스에 올린다(항상 draft — 실패해도 텍스트
    생성 자체는 성공 처리하고 output_data.wp_auto_publish에 결과만 기록).
    """
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)

    try:
        from services.claude_service import generate_text
        text = generate_text(system_prompt, user_prompt, max_tokens=max_tokens)

        if mode == 'google':
            naver_text, google_text = '', text.strip()
        else:
            naver_text, google_text = split_naver_google(text, both=True)
        naver_text = _apply_disclaimer(naver_text, disclaimer)
        google_text = _apply_disclaimer(google_text, disclaimer)

        wp_auto_publish = None
        if brand_id and google_text:
            try:
                from services.wordpress_publish import create_google_post
                wp_auto_publish = create_google_post(
                    supabase, brand_id, google_text, status='draft')
            except Exception as e:
                logger.warning('[blog_text_task] 자동 발행 실패(무시) cid=%s: %s', creation_id, e)
                wp_auto_publish = {'ok': False, 'message': str(e)[:200]}

        output_data = {'text': naver_text, 'google_text': google_text}
        if wp_auto_publish is not None:
            output_data['wp_auto_publish'] = wp_auto_publish

        supabase.table('creations').update({
            'status': 'done',
            'output_data': output_data,
        }).eq('id', creation_id).execute()

        logger.info('[blog_text_task] 생성 완료 cid=%s wp_auto=%s', creation_id,
                   (wp_auto_publish or {}).get('ok'))

    except Exception as e:
        logger.error('[blog_text_task] 오류 cid=%s: %s', creation_id, e, exc_info=True)
        from services.async_generation import mark_task_failed
        mark_task_failed(supabase, creation_id, e,
                         '블로그 본문 생성 실패 — 자동 환불', user_id)
        raise
