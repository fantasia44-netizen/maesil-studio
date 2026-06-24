"""상세페이지 초안 기획 (3타입) Celery 태스크"""
import json
import logging

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, name='tasks.detail_page_task.generate_plan', max_retries=1,
             soft_time_limit=300, time_limit=360)
def generate_plan(
    self,
    plan_id: str,
    user_id: str,
    operator_id,
    brand: dict,
    input_data: dict,
    cost: int,
    supabase_url: str,
    supabase_key: str,
    anthropic_api_key: str,
):
    """Celery 워커에서 Claude 3타입 상세페이지 기획 생성."""
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)

    try:
        from services.prompts.detail_page import build_single_plan_prompt, _PLAN_TYPES
        from services.claude_service import generate_text

        os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)

        plans = []
        for type_name in _PLAN_TYPES:
            system, user_prompt = build_single_plan_prompt(brand, input_data, type_name)
            raw = generate_text(system, user_prompt, max_tokens=3000,
                                model='claude-sonnet-4-6')
            cleaned = raw.strip()
            if cleaned.startswith('```'):
                cleaned = cleaned.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
            plan = json.loads(cleaned)
            plans.append(plan)
            logger.info('[dp_plan_task] plan "%s" 완료', type_name)

        plans_data = {'plans': plans}

        supabase.table('creations').update({
            'output_data': plans_data,
            'status': 'done',
        }).eq('id', plan_id).execute()

        logger.info('[dp_plan_task] 전체 완료 plan_id=%s', plan_id)

    except Exception as e:
        logger.error('[dp_plan_task] 오류 plan_id=%s: %s', plan_id, e, exc_info=True)
        try:
            err_msg = str(e)[:200]
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {'error': err_msg},
            }).eq('id', plan_id).execute()
        except Exception:
            pass
        raise
