"""생성 공통 헬퍼"""
import uuid
import logging
from flask import current_app, jsonify
from flask_login import current_user
from services.tz_utils import now_kst
from services.point_service import use_points, InsufficientPoints
from models import POINT_COSTS

logger = logging.getLogger(__name__)


def get_accessible_brands(supabase) -> list:
    """사용자가 접근 가능한 브랜드 목록
    - operator 소속 + admin → operator 전체 브랜드
    - operator 소속 + 일반 직원 → user_brand_access 배정 브랜드 (없으면 전체)
    - 개인 사용자 → 본인 브랜드만
    """
    user = current_user
    if user.operator_id:
        if user.is_operator_admin:
            # operator 브랜드 + 본인이 직접 만든 브랜드 모두 포함
            r1 = supabase.table('brand_profiles').select('*').eq(
                'operator_id', user.operator_id
            ).execute()
            r2 = supabase.table('brand_profiles').select('*').eq(
                'user_id', user.id
            ).is_('operator_id', 'null').execute()
            seen, brands = set(), []
            for b in (r1.data or []) + (r2.data or []):
                if b['id'] not in seen:
                    seen.add(b['id'])
                    brands.append(b)
            brands.sort(key=lambda b: (not b.get('is_default'), b.get('created_at', '')))
            return brands
        # 일반 직원: 배정된 브랜드 확인
        access = supabase.table('user_brand_access').select('brand_id').eq(
            'user_id', user.id
        ).execute()
        brand_ids = [r['brand_id'] for r in (access.data or [])]
        if brand_ids:
            result = supabase.table('brand_profiles').select('*').in_(
                'id', brand_ids
            ).execute()
        else:
            result = supabase.table('brand_profiles').select('*').eq(
                'operator_id', user.operator_id
            ).order('is_default', desc=True).execute()
        return result.data or []
    # 개인 사용자
    result = supabase.table('brand_profiles').select('*').eq(
        'user_id', user.id
    ).order('is_default', desc=True).execute()
    return result.data or []


def get_default_brand(supabase):
    brands = get_accessible_brands(supabase)
    if not brands:
        return None
    default = next((b for b in brands if b.get('is_default')), None)
    return default or brands[0]


def get_brand_by_id(supabase, brand_id: str):
    accessible_ids = {b['id'] for b in get_accessible_brands(supabase)}
    if brand_id not in accessible_ids:
        return None
    result = supabase.table('brand_profiles').select('*').eq('id', brand_id).execute()
    return result.data[0] if result.data else None


def run_text_generation(creation_type: str, brand: dict, input_data: dict,
                        system_prompt: str, user_prompt: str,
                        *,
                        point_cost: int | None = None,
                        ledger_note: str | None = None,
                        extra_creation_fields: dict | None = None,
                        post_process=None,
                        max_tokens: int = 4096) -> dict:
    """텍스트 생성 공통 플로우 (포인트 차감 + DB 저장).

    Args:
      point_cost: 동적 비용. None 이면 POINT_COSTS[creation_type] 사용.
      ledger_note: 포인트 ledger 메모 (예: '블로그 (1,000자)').
      extra_creation_fields: creations 테이블에 추가 저장할 컬럼들.
                             예: {'product_id': '...', 'angle': 'review',
                                  'topic': '이유식', 'keyword': '야채큐브',
                                  'length_chars': 1000, 'relation_mode': 'series',
                                  'relation_ref_id': '...'}
      post_process: callable(output_text: str) -> str — 결과 후처리 (디스클레이머 부착 등).
      max_tokens: Claude max_tokens.
    """
    supabase = current_app.supabase
    creation_id = str(uuid.uuid4())
    cost = point_cost if point_cost is not None else POINT_COSTS.get(creation_type, 0)

    # creation 행 생성 (generating)
    insert_row = {
        'id': creation_id,
        'user_id': current_user.id,
        'brand_id': brand['id'],
        'creation_type': creation_type,
        'input_data': input_data,
        'output_data': {},
        'points_used': cost,
        'status': 'generating',
        'model_used': 'claude-sonnet-4-6',
        'created_at': now_kst().isoformat(),
    }
    if extra_creation_fields:
        insert_row.update({k: v for k, v in extra_creation_fields.items() if v is not None})
    try:
        supabase.table('creations').insert(insert_row).execute()
    except Exception as e:
        # 신규 컬럼(product_id/angle 등)이 아직 마이그레이션 전인 환경 대비 — 기본 컬럼만 재시도
        logger.warning(f'[CREATE] insert with extra fields failed, retry minimal: {e}')
        minimal = {k: v for k, v in insert_row.items()
                   if k in {'id', 'user_id', 'brand_id', 'creation_type',
                            'input_data', 'output_data', 'points_used',
                            'status', 'model_used', 'created_at'}}
        supabase.table('creations').insert(minimal).execute()

    import time
    start = time.time()
    try:
        # 포인트 차감
        use_points(current_user.id, creation_type, creation_id,
                   cost_override=cost, note_override=ledger_note)

        # Claude 호출
        from services.claude_service import generate_text
        output_text = generate_text(system_prompt, user_prompt, max_tokens=max_tokens)

        # 후처리 (디스클레이머 부착 등)
        if callable(post_process):
            try:
                output_text = post_process(output_text) or output_text
            except Exception as ppe:
                logger.warning(f'[CREATE] post_process 실패 (원문 유지): {ppe}')

        gen_ms = int((time.time() - start) * 1000)

        # creation 업데이트
        supabase.table('creations').update({
            'output_data': {'text': output_text},
            'status': 'done',
            'generation_ms': gen_ms,
        }).eq('id', creation_id).execute()

        return {'ok': True, 'creation_id': creation_id, 'text': output_text}

    except InsufficientPoints as ip:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return {'ok': False, 'error': 'points', 'message': str(ip) or '포인트가 부족합니다.'}
    except Exception as e:
        logger.error(f'[CREATE] {creation_type} error: {e}')
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return {'ok': False, 'error': 'api', 'message': 'AI 생성 중 오류가 발생했습니다.'}
