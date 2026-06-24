"""상세페이지 초안 Celery 태스크"""
import json
import logging

from celery_app import celery

logger = logging.getLogger(__name__)


def _setup():
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)


# ── Phase 1: 3타입 미리보기 생성 ────────────────────────────
@celery.task(bind=True, name='tasks.detail_page_task.generate_plan',
             max_retries=1, soft_time_limit=120, time_limit=150)
def generate_plan(self, plan_id, user_id, operator_id, brand, input_data,
                  cost, supabase_url, supabase_key, anthropic_api_key):
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)

    try:
        from services.prompts.detail_page import build_preview_prompt
        from services.claude_service import generate_text

        system, user_prompt = build_preview_prompt(brand, input_data)
        raw = generate_text(system, user_prompt, max_tokens=3000,
                            model='claude-sonnet-4-6')

        cleaned = raw.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        data = json.loads(cleaned)

        supabase.table('creations').update(
            {'output_data': data, 'status': 'done'}
        ).eq('id', plan_id).execute()

        logger.info('[dp_plan] 미리보기 완료 plan_id=%s', plan_id)

    except Exception as e:
        logger.error('[dp_plan] 오류 plan_id=%s: %s', plan_id, e, exc_info=True)
        try:
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {'error': str(e)[:200]},
            }).eq('id', plan_id).execute()
        except Exception:
            pass
        raise


# ── Phase 2: 선택된 타입 카피 생성 ──────────────────────────
@celery.task(bind=True, name='tasks.detail_page_task.generate_copy',
             max_retries=1, soft_time_limit=120, time_limit=150)
def generate_copy(self, draft_id, brand, input_data, plan_preview,
                  supabase_url, supabase_key, anthropic_api_key):
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)

    try:
        from services.prompts.detail_page import build_copy_prompt
        from services.claude_service import generate_text

        system, user_prompt = build_copy_prompt(brand, input_data, plan_preview)
        raw = generate_text(system, user_prompt, max_tokens=2000,
                            model='claude-sonnet-4-6')

        cleaned = raw.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        result = json.loads(cleaned)

        # copies를 sections에 병합
        copies = {c['no']: c['copy'] for c in result.get('copies', [])}
        r = supabase.table('creations').select('output_data').eq('id', draft_id).single().execute()
        od = r.data.get('output_data') or {}
        for sec in od.get('sections', []):
            if sec['no'] in copies:
                sec['copy'] = copies[sec['no']]
        od['copy_status'] = 'done'

        supabase.table('creations').update(
            {'output_data': od, 'status': 'done'}
        ).eq('id', draft_id).execute()

        logger.info('[dp_copy] 카피 완료 draft_id=%s', draft_id)

    except Exception as e:
        logger.error('[dp_copy] 오류 draft_id=%s: %s', draft_id, e, exc_info=True)
        try:
            r = supabase.table('creations').select('output_data').eq('id', draft_id).single().execute()
            od = r.data.get('output_data') or {}
            od['copy_status'] = 'failed'
            od['copy_error'] = str(e)[:200]
            supabase.table('creations').update({'output_data': od}).eq('id', draft_id).execute()
        except Exception:
            pass
        raise
