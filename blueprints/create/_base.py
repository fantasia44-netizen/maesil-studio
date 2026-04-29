"""생성 공통 헬퍼"""
import uuid
import logging
from flask import current_app, jsonify
from flask_login import current_user
from services.tz_utils import now_kst
from services.point_service import use_points, InsufficientPoints
from models import POINT_COSTS

logger = logging.getLogger(__name__)


def get_default_brand(supabase):
    """사용자의 기본 브랜드 프로필 반환"""
    result = supabase.table('brand_profiles').select('*').eq(
        'user_id', current_user.id
    ).eq('is_default', True).limit(1).execute()
    if result.data:
        return result.data[0]
    # 기본 없으면 첫 번째
    result2 = supabase.table('brand_profiles').select('*').eq(
        'user_id', current_user.id
    ).limit(1).execute()
    return result2.data[0] if result2.data else None


def get_brand_by_id(supabase, brand_id: str):
    result = supabase.table('brand_profiles').select('*').eq(
        'id', brand_id
    ).eq('user_id', current_user.id).execute()
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
