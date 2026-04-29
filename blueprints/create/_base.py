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
            result = supabase.table('brand_profiles').select('*').eq(
                'operator_id', user.operator_id
            ).order('is_default', desc=True).execute()
            return result.data or []
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
                        system_prompt: str, user_prompt: str) -> dict:
    """텍스트 생성 공통 플로우 (포인트 차감 + DB 저장)"""
    supabase = current_app.supabase
    creation_id = str(uuid.uuid4())

    # creation 행 생성 (generating)
    supabase.table('creations').insert({
        'id': creation_id,
        'user_id': current_user.id,
        'brand_id': brand['id'],
        'creation_type': creation_type,
        'input_data': input_data,
        'output_data': {},
        'points_used': POINT_COSTS[creation_type],
        'status': 'generating',
        'model_used': 'claude-sonnet-4-6',
        'created_at': now_kst().isoformat(),
    }).execute()

    import time
    start = time.time()
    try:
        # 포인트 차감
        use_points(current_user.id, creation_type, creation_id)

        # Claude 호출
        from services.claude_service import generate_text
        output_text = generate_text(system_prompt, user_prompt)

        gen_ms = int((time.time() - start) * 1000)

        # creation 업데이트
        supabase.table('creations').update({
            'output_data': {'text': output_text},
            'status': 'done',
            'generation_ms': gen_ms,
        }).eq('id', creation_id).execute()

        return {'ok': True, 'creation_id': creation_id, 'text': output_text}

    except InsufficientPoints:
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return {'ok': False, 'error': 'points', 'message': '포인트가 부족합니다.'}
    except Exception as e:
        logger.error(f'[CREATE] {creation_type} error: {e}')
        supabase.table('creations').update({'status': 'failed'}).eq('id', creation_id).execute()
        return {'ok': False, 'error': 'api', 'message': 'AI 생성 중 오류가 발생했습니다.'}
