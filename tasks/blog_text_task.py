"""일반 블로그 "네이버+구글(워드프레스) 세트" 본문 생성 Celery 태스크.

네이버 단독 생성은 blueprints/create/blog.py에서 기존처럼 동기(run_text_generation)로
처리하고, 이 태스크는 출력이 2배로 늘어나는 '세트' 옵션에서만 쓰인다
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
                       disclaimer=''):
    """블로그 본문 — 네이버판 + 구글(워드프레스)판을 함께 생성."""
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)

    try:
        from services.claude_service import generate_text
        text = generate_text(system_prompt, user_prompt, max_tokens=max_tokens)

        naver_text, google_text = split_naver_google(text, both=True)
        naver_text = _apply_disclaimer(naver_text, disclaimer)
        google_text = _apply_disclaimer(google_text, disclaimer)

        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'text': naver_text, 'google_text': google_text},
        }).eq('id', creation_id).execute()

        logger.info('[blog_text_task] 생성 완료 cid=%s', creation_id)

    except Exception as e:
        logger.error('[blog_text_task] 오류 cid=%s: %s', creation_id, e, exc_info=True)
        from services.async_generation import mark_task_failed
        mark_task_failed(supabase, creation_id, e,
                         '블로그 본문 생성 실패 — 자동 환불', user_id)
        raise
