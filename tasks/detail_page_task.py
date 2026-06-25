"""상세페이지 초안 Celery 태스크"""
import json
import logging
import io

from celery_app import celery

logger = logging.getLogger(__name__)


def _setup():
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)


# ── Phase 0: 상품 진단 ──────────────────────────────────────
@celery.task(bind=True, name='tasks.detail_page_task.diagnose_product',
             max_retries=1, soft_time_limit=60, time_limit=90)
def diagnose_product(self, diag_id, brand, input_data,
                     supabase_url, supabase_key, anthropic_api_key):
    """상품 진단 — 타입 추천(★점수) + 핵심 구매이유/망설임 추출."""
    _setup()
    import os
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    os.environ.setdefault('ANTHROPIC_API_KEY', anthropic_api_key)

    try:
        from services.prompts.detail_page import build_diagnosis_prompt
        from services.claude_service import generate_text

        system, user_prompt = build_diagnosis_prompt(brand, input_data)
        raw = generate_text(system, user_prompt, max_tokens=1000,
                            model='claude-haiku-4-5-20251001')

        cleaned = raw.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        data = json.loads(cleaned)

        supabase.table('creations').update(
            {'output_data': data, 'status': 'done'}
        ).eq('id', diag_id).execute()

        logger.info('[dp_diag] 진단 완료 diag_id=%s', diag_id)

    except Exception as e:
        logger.error('[dp_diag] 오류 diag_id=%s: %s', diag_id, e, exc_info=True)
        try:
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {'error': str(e)[:200]},
            }).eq('id', diag_id).execute()
        except Exception:
            pass
        raise


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
        from services.prompts.detail_page import build_copy_prompt, build_review_prompt
        from services.claude_service import generate_text

        system, user_prompt = build_copy_prompt(brand, input_data, plan_preview)
        raw = generate_text(system, user_prompt, max_tokens=2000,
                            model='claude-sonnet-4-6')

        cleaned = raw.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        result = json.loads(cleaned)

        # ── Phase 2.5: AI 자기검수 ───────────────────────────
        raw_copies = result.get('copies', [])
        try:
            rev_sys, rev_user = build_review_prompt(raw_copies, input_data, plan_preview)
            rev_raw = generate_text(rev_sys, rev_user, max_tokens=1500,
                                    model='claude-haiku-4-5-20251001')
            rev_cleaned = rev_raw.strip()
            if rev_cleaned.startswith('```'):
                rev_cleaned = rev_cleaned.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
            review = json.loads(rev_cleaned)

            # 재작성 섹션 병합
            revisions = {r['no']: r['copy'] for r in review.get('revisions', [])}
            if revisions:
                for c in raw_copies:
                    if c['no'] in revisions:
                        c['copy'] = revisions[c['no']]
                logger.info('[dp_copy] 검수 완료 — 재작성 섹션: %s', list(revisions.keys()))
            result['review'] = {
                'overall_pass': review.get('overall_pass', True),
                'checks': review.get('checks', []),
                'revised_sections': list(revisions.keys()),
            }
        except Exception as rev_e:
            logger.warning('[dp_copy] 검수 실패 (무시): %s', rev_e)
            result['review'] = {'overall_pass': True, 'checks': [], 'revised_sections': []}

        # scene_prompt + commerce_prompt → image_prompt 합성 (FLUX 전송용)
        _FLUX_SUFFIX = (
            'high-end commercial photography, 8k resolution, photorealistic, '
            'NO text NO labels NO writing on any surface, blank clean package branding, '
            'sharp focus, shot on 35mm lens'
        )
        _VISIBILITY_PREFIX = {
            'none':   'Warm natural lifestyle photography, emotional atmosphere, authentic feel, soft bokeh,',
            'small':  'Warm natural lifestyle photography, product subtly placed in background, soft bokeh,',
            'medium': 'Commercial lifestyle photography, product clearly visible alongside person,',
            'large':  'Minimalist product studio lighting, product package as hero in foreground, bright clean background,',
        }

        def _build_flux_prompt(c: dict, sec: dict) -> str:
            scene    = c.get('scene_prompt') or ''
            commerce = c.get('commerce_prompt') or ''
            legacy   = c.get('image_prompt') or ''   # 이전 포맷 호환
            visibility = sec.get('product_visibility', 'medium')
            prefix = _VISIBILITY_PREFIX.get(visibility, _VISIBILITY_PREFIX['medium'])
            body = f"{scene}, {commerce}" if scene or commerce else legacy
            return f"{prefix} {body}, {_FLUX_SUFFIX}"

        # copies + image_prompt를 sections에 병합
        sec_map = {sec['no']: sec for sec in
                   (supabase.table('creations').select('output_data')
                    .eq('id', draft_id).single().execute().data or {}).get('output_data', {}).get('sections', [])}
        copies = {}
        for c in raw_copies:
            sec = sec_map.get(c['no'], {})
            copies[c['no']] = (c['copy'], _build_flux_prompt(c, sec))

        r = supabase.table('creations').select('output_data').eq('id', draft_id).single().execute()
        od = r.data.get('output_data') or {}
        for sec in od.get('sections', []):
            if sec['no'] in copies:
                sec['copy'], sec['image_prompt'] = copies[sec['no']]
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


# ── Phase 3: PNG/PDF 합성 및 Supabase Storage 업로드 ─────────
@celery.task(bind=True, name='tasks.detail_page_task.export_draft',
             max_retries=1, soft_time_limit=180, time_limit=210)
def export_draft(self, draft_id, fmt, supabase_url, supabase_key):
    """PNG/PDF 합성 → Supabase Storage(creations 버킷) 업로드 → URL 저장."""
    _setup()
    import uuid as _uuid
    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)

    try:
        r = supabase.table('creations').select('output_data, user_id').eq('id', draft_id).single().execute()
        od      = r.data.get('output_data') or {}
        user_id = r.data.get('user_id', 'unknown')

        from services.detail_page_draft_service import compose_draft_png, compose_draft_pdf
        if fmt == 'pdf':
            data_bytes   = compose_draft_pdf(od)
            content_type = 'application/pdf'
            ext          = 'pdf'
        else:
            data_bytes   = compose_draft_png(od)
            content_type = 'image/png'
            ext          = 'png'

        path = f'{user_id}/{_uuid.uuid4().hex}_detail_export.{ext}'
        supabase.storage.from_('creations').upload(
            path, data_bytes, {'content-type': content_type}
        )
        url = supabase.storage.from_('creations').get_public_url(path)

        od[f'export_{ext}_url'] = url
        supabase.table('creations').update({'output_data': od}).eq('id', draft_id).execute()

        logger.info('[dp_export] 완료 draft_id=%s fmt=%s size=%d', draft_id, fmt, len(data_bytes))
        return {'ok': True, 'url': url}

    except Exception as e:
        logger.error('[dp_export] 오류 draft_id=%s: %s', draft_id, e, exc_info=True)
        # 실패 상태를 output_data에 기록해서 폴링이 감지할 수 있게
        try:
            r2 = supabase.table('creations').select('output_data').eq('id', draft_id).single().execute()
            od2 = r2.data.get('output_data') or {}
            od2[f'export_{fmt}_error'] = str(e)[:200]
            supabase.table('creations').update({'output_data': od2}).eq('id', draft_id).execute()
        except Exception:
            pass
        raise
