"""경험담 블로그 생성 Celery 태스크.

메인 웹서버에서 20~90초 걸리는 Claude(비전) 호출을 블로킹하지 않도록 워커로 분리.
detail_page_task.generate_plan 패턴을 그대로 따르되, 실패 시 shorts 처럼 포인트 환불.

흐름:
  1) 라우트에서 포인트 차감 + creations(status='generating') INSERT 후 .delay() 제출
  2) 워커: Claude 호출 → 네이버/구글판 분리 → creations UPDATE(status='done')
  3) 실패: status='failed' + output_data.error 기록 + 포인트 자동 환불
"""
import logging

from celery_app import celery
from services.text_split import split_naver_google as _split_naver_google

logger = logging.getLogger(__name__)


def _setup():
    import os, sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)


def _refund_experience_points(supabase, creation_id: str, user_id: str) -> None:
    """경험담 생성 실패 시 차감 포인트 환불 (shorts 패턴).

    point_ledger 최신 balance 에 pts 를 더한 새 행 INSERT.
    """
    row = supabase.table('creations').select(
        'points_used, operator_id'
    ).eq('id', creation_id).maybe_single().execute()
    if not row or not row.data:
        return
    pts = int(row.data.get('points_used') or 0)
    if pts <= 0:
        return

    operator_id = row.data.get('operator_id')

    if operator_id:
        bal_row = supabase.table('point_ledger').select('balance').eq(
            'operator_id', operator_id
        ).order('created_at', desc=True).limit(1).execute()
    else:
        bal_row = supabase.table('point_ledger').select('balance').eq(
            'user_id', user_id
        ).is_('operator_id', 'null').order('created_at', desc=True).limit(1).execute()

    current = (bal_row.data[0].get('balance', 0)) if bal_row and bal_row.data else 0
    new_bal = current + pts

    ledger_row = {
        'user_id': user_id,
        'type':    'refund',
        'amount':  pts,
        'balance': new_bal,
        'ref_id':  creation_id,
        'note':    '경험담 블로그 생성 실패 — 자동 환불',
    }
    if operator_id:
        ledger_row['operator_id'] = operator_id
    supabase.table('point_ledger').insert(ledger_row).execute()
    logger.info('[exp_task] 포인트 환불 완료: %dP → user=%s (잔액 %d)', pts, user_id, new_bal)


@celery.task(bind=True, name='tasks.experience_task.generate_experience',
             max_retries=1, soft_time_limit=240, time_limit=300)
def generate_experience(self, creation_id, user_id, system_prompt, user_prompt,
                        images, max_tokens, both,
                        supabase_url, supabase_key, anthropic_api_key,
                        brand_id=None):
    """사진+메모 → 경험담 글 생성 (네이버판 + 선택적 구글판).

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
        if images:
            from services.claude_service import generate_with_images
            # JSON 직렬화로 (b64, mt) 튜플이 [b64, mt] 리스트가 되므로 튜플로 복원
            img_tuples = [(pair[0], pair[1]) for pair in images]
            text = generate_with_images(system_prompt, user_prompt, img_tuples,
                                        max_tokens=max_tokens)
        else:
            from services.claude_service import generate_text
            text = generate_text(system_prompt, user_prompt, max_tokens=max_tokens)

        naver_text, google_text = _split_naver_google(text, both)

        wp_auto_publish = None
        if brand_id and google_text:
            try:
                from services.wordpress_publish import create_google_post
                wp_auto_publish = create_google_post(
                    supabase, brand_id, google_text, status='draft')
            except Exception as e:
                logger.warning('[exp_task] 자동 발행 실패(무시) cid=%s: %s', creation_id, e)
                wp_auto_publish = {'ok': False, 'message': str(e)[:200]}

        output_data = {'text': naver_text, 'google_text': google_text}
        if wp_auto_publish is not None:
            output_data['wp_auto_publish'] = wp_auto_publish

        supabase.table('creations').update({
            'status': 'done',
            'output_data': output_data,
        }).eq('id', creation_id).execute()

        logger.info('[exp_task] 생성 완료 cid=%s photos=%d both=%s wp_auto=%s',
                    creation_id, len(images or []), both, (wp_auto_publish or {}).get('ok'))

    except Exception as e:
        logger.error('[exp_task] 오류 cid=%s: %s', creation_id, e, exc_info=True)
        try:
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {'error': str(e)[:200]},
            }).eq('id', creation_id).execute()
        except Exception:
            pass
        try:
            _refund_experience_points(supabase, creation_id, user_id)
        except Exception as ref_e:
            logger.error('[exp_task] 포인트 환불 오류: %s', ref_e)
        raise
