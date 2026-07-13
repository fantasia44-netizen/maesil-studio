"""경험담 블로그 — 실사진 + 대충 쓴 메모 → 완성된 경험담 글.

기존 블로그(마케팅형)와 반대 방향: AI가 내용을 '창작'하지 않고, 사용자가 겪은
실경험(사진·메모)을 '정리·구성'만 한다. 실사진+실경험은 네이버 검색이 우대하는
신호라 유사문서·저품질 리스크가 구조적으로 낮다.

  · 입력: 실사진(순서 있음, 최대 10장) + 메모 + 유형(여행/맛집/후기/체험)
  · Claude Vision이 사진 내용을 읽고 메모와 엮어 [사진 N] 마커 포함 글 생성
  · AI 이미지 생성 없음 → 원가는 vision 토큰뿐
"""
import base64
import logging
import re
import uuid as _uuid
from flask import render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from blueprints.create import create_bp
from models import POINT_COSTS
from services.tz_utils import now_kst
from services.rate_limiter import check_ai_rate_limit

logger = logging.getLogger(__name__)

_MAX_PHOTOS = 10

# 유형별 스토리 구조 힌트
_TYPE_GUIDES = {
    'travel': ('여행기', '이동/도착 → 둘러본 순서(동선·시간순) → 인상 깊었던 순간 → 팁(주차·예약·비용) → 총평'),
    'food':   ('맛집·카페 방문기', '가게 외관/위치 → 내부 분위기 → 주문 메뉴 → 맛·양·가격 솔직 후기 → 재방문 의사·팁'),
    'review': ('제품 사용 후기', '구매 계기 → 개봉/첫인상 → 실제 사용 장면 → 좋았던 점/아쉬운 점 → 추천 대상'),
    'place':  ('장소·체험 후기', '예약/입장 과정 → 체험 내용(순서대로) → 아이/동행인 반응 → 꿀팁 → 총평'),
}

_TONES = {
    'friendly': '친근하고 발랄한 구어체(적당한 이모지, ~했어요체)',
    'calm':     '차분하고 담백한 문체(이모지 최소, ~했다/~였다 혼용)',
    'info':     '정보 전달 중심(항목 정리·팁 강조, 담백한 ~해요체)',
}


@create_bp.route('/experience')
@login_required
def experience():
    """경험담 블로그 작성 페이지."""
    cost = POINT_COSTS.get('experience_blog', 150)
    return render_template('create/experience.html', cost=cost)


@create_bp.route('/experience/generate', methods=['POST'])
@login_required
def experience_generate():
    """사진+메모 → 경험담 블로그 글 생성.

    Request JSON:
      exp_type  str   travel|food|review|place
      memo      str   대충 쓴 경험 메모 (필수)
      place     str   장소/상호/제품명 (선택)
      visit_at  str   방문/사용 시기 (선택, 예: '2026년 7월 초')
      tone      str   friendly|calm|info
      photos    list  base64 data URL 목록 (순서 유지, 1~10장)
    """
    err = check_ai_rate_limit('experience_blog', max_per_hour=30)
    if err:
        return jsonify(ok=False, message=err), 429

    data = request.get_json(force=True) or {}
    memo = (data.get('memo') or '').strip()
    if not memo:
        return jsonify(ok=False, message='경험 메모를 입력해 주세요. 대충 쓰셔도 됩니다.')
    photos = data.get('photos') if isinstance(data.get('photos'), list) else []
    photos = [p for p in photos if isinstance(p, str) and p.startswith('data:image/')][:_MAX_PHOTOS]
    if not photos:
        return jsonify(ok=False, message='사진을 1장 이상 올려주세요. 실사진이 이 글의 핵심입니다.')

    exp_type = data.get('exp_type') if data.get('exp_type') in _TYPE_GUIDES else 'place'
    tone     = data.get('tone') if data.get('tone') in _TONES else 'friendly'
    place    = (data.get('place') or '').strip()[:60]
    visit_at = (data.get('visit_at') or '').strip()[:40]

    # 포인트 확인·차감 준비
    cost = POINT_COSTS.get('experience_blog', 150)
    from services.point_service import get_balance, use_points, InsufficientPoints
    balance = get_balance(current_user)
    if balance < cost:
        return jsonify(ok=False, message=f'포인트가 부족합니다. (필요: {cost}P, 잔액: {balance}P)')

    # data URL → (b64, media_type)
    images = []
    for p in photos:
        try:
            header, b64 = p.split(',', 1)
            mt = header.split(';')[0].split(':')[1] or 'image/jpeg'
            base64.b64decode(b64[:80])          # 형식 검증만 (앞부분)
            images.append((b64, mt))
        except Exception:
            continue
    if not images:
        return jsonify(ok=False, message='사진 형식을 읽을 수 없습니다. 다시 업로드해 주세요.')

    type_label, structure = _TYPE_GUIDES[exp_type]
    system_prompt = (
        '당신은 네이버 블로그 경험담 전문 에디터입니다. 사용자가 올린 실제 사진들과 '
        '대충 쓴 메모를 바탕으로, 직접 겪은 사람의 목소리로 자연스러운 경험담 글을 씁니다.\n'
        '\n'
        '절대 규칙:\n'
        '- 사진에서 보이는 것과 메모에 적힌 것만 쓴다. 확인할 수 없는 사실(가격·영업시간·'
        '지명 등)은 창작하지 않는다. 메모에 없으면 그냥 언급하지 않는다.\n'
        '- 광고 문구·과장("인생 맛집", "무조건 가세요" 남발) 금지. 솔직한 장단점.\n'
        '- 각 사진이 들어갈 자리에 [사진 N] 마커를 한 줄로 단독 배치한다. '
        '모든 사진을 순서대로 1회씩 사용한다.\n'
        '- 네이버 블로그 스타일: 2~4문장짜리 짧은 문단, 문단 사이 빈 줄, 소제목 활용.\n'
        '- 마크다운 기호(#, **, -) 대신 일반 텍스트와 줄바꿈만 사용한다(네이버 에디터 복붙용).'
    )
    user_prompt = (
        f'글 유형: {type_label}\n'
        f'권장 구성: {structure}\n'
        f'문체: {_TONES[tone]}\n'
        + (f'장소/제품명: {place}\n' if place else '')
        + (f'시기: {visit_at}\n' if visit_at else '')
        + '\n[내 메모 — 이 경험이 글의 재료입니다]\n'
        + memo[:2000]
        + '\n\n위 사진들을 순서대로 살펴보고, 사진 내용과 메모를 엮어 경험담 블로그 글을 '
          '작성하세요.\n'
          '출력 형식:\n'
          '제목 후보 3개(각 25자 내외, 검색어가 앞에 오게) → 빈 줄 → 본문(사진 마커 포함) '
          '→ 마지막에 해시태그 8~12개(#태그 형식 한 줄).'
    )

    from services.claude_service import generate_with_images
    try:
        text = generate_with_images(system_prompt, user_prompt, images, max_tokens=4096)
    except Exception as e:
        logger.error(f'[experience] 생성 실패: {e}', exc_info=True)
        return jsonify(ok=False, message=f'글 생성에 실패했습니다. ({str(e)[:100]})')

    # 포인트 차감 + creations 기록
    cid = str(_uuid.uuid4())
    try:
        use_points(current_user, 'experience_blog', cid)
    except InsufficientPoints as e:
        return jsonify(ok=False, message=str(e))
    except Exception as e:
        logger.error(f'[experience] 포인트 차감 실패: {e}', exc_info=True)

    try:
        if current_app.supabase:
            now_s = now_kst().isoformat()
            row = {
                'id': cid, 'user_id': current_user.id,
                'creation_type': 'experience_blog',
                'input_data': {'exp_type': exp_type, 'tone': tone, 'place': place,
                               'visit_at': visit_at, 'memo': memo[:500],
                               'photo_count': len(images)},
                'output_data': {'text': text},
                'points_used': cost, 'status': 'done',
                'created_at': now_s, 'updated_at': now_s,
            }
            if getattr(current_user, 'operator_id', None):
                row['operator_id'] = current_user.operator_id
            current_app.supabase.table('creations').insert(row).execute()
    except Exception as e:
        logger.warning(f'[experience] 이력 기록 실패(무시): {e}')

    logger.info(f'[experience] 생성 완료 uid={str(current_user.id)[:8]} '
                f'type={exp_type} photos={len(images)} cost={cost}P')
    return jsonify(ok=True, text=text, cost=cost)
