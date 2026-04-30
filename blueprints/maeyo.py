"""매요 AI 채팅 프록시 — maesil-agency /api/cs/chat 중계."""
import logging
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)
maeyo_bp = Blueprint('maeyo', __name__)


def _build_user_context() -> dict:
    """현재 로그인 사용자의 컨텍스트를 매요 AI에 전달할 형태로 구성."""
    ctx: dict = {
        "plan_type":        current_user.plan_type,
        "company_name":     "",
        "connected_channels": [],
        "has_coupang_ad":   False,
        "has_naver_ad":     False,
    }

    supabase = current_app.supabase
    if not supabase:
        return ctx

    # operator 이름
    if current_user.operator_id:
        try:
            op = supabase.table('operators').select('name').eq(
                'id', current_user.operator_id
            ).limit(1).execute()
            if op.data:
                ctx["company_name"] = op.data[0].get('name', '')
        except Exception:
            pass

    # 매실 인사이트 연결 여부
    try:
        conn = supabase.table('maesil_insight_connections').select('id').eq(
            'user_id', str(current_user.id)
        ).limit(1).execute()
        if conn.data:
            ctx["connected_channels"] = ["매실 인사이트"]
    except Exception:
        pass

    return ctx


@maeyo_bp.route('/maeyo/chat', methods=['POST'])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify(ok=False, error='메시지를 입력하세요.'), 400

    from services.maeyo_client import chat as maeyo_chat
    result = maeyo_chat(
        message=message,
        history=data.get('history') or [],
        user_context=_build_user_context(),
        operator_id=str(current_user.operator_id or ''),
        user_id=str(current_user.id),
        conversation_id=data.get('conversation_id'),
        program='maesil-studio',
    )

    return jsonify(ok=True, **result)
