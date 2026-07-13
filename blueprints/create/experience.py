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
    'friendly': '친근하고 발랄한 구어체. 문장 어미는 "~했어요/~더라고요/~였어요"체로 처음부터 끝까지 통일. 이모지는 문단당 최대 1개.',
    'calm':     '차분하고 담백한 문체. 문장 어미는 "~했다/~였다"(평서형)로 통일. 이모지 사용 안 함.',
    'info':     '정보 전달 중심의 담백한 "~해요"체로 통일. 팁·항목은 자연스러운 문장으로 풀되 기계적 나열은 피함.',
}

# 분량: (라벨, 네이버판 목표, 구글판 목표, max_tokens[단일판 기준])
#   구글판은 네이버판의 ~1.5배 + 표/FAQ — 구글은 깊이가 랭킹 요소.
_LENGTHS = {
    'short':  ('짧게', '본문 1,000~1,200자', '본문 1,800~2,200자', 3500),
    'medium': ('보통', '본문 1,800~2,200자', '본문 3,000~3,500자', 5500),
    'long':   ('길게', '본문 2,500~3,000자', '본문 4,000~5,000자', 8000),
}


@create_bp.route('/experience')
@login_required
def experience():
    """경험담 블로그 작성 페이지."""
    cost = POINT_COSTS.get('experience_blog', 150)
    from services.wordpress_connection import is_connected as wp_is_connected
    wp_connected = wp_is_connected(
        current_user.id, operator_id=getattr(current_user, 'operator_id', None))
    return render_template('create/experience.html', cost=cost,
                           wp_connected=wp_connected)


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
    # 사진은 선택 — 없으면 텍스트 경험만으로 정리 (온라인 업무 경험담 등)

    exp_type = data.get('exp_type') if data.get('exp_type') in _TYPE_GUIDES else 'place'
    tone     = data.get('tone') if data.get('tone') in _TONES else 'friendly'
    length   = data.get('length') if data.get('length') in _LENGTHS else 'medium'
    both     = data.get('targets') != 'naver'   # 기본: 네이버+구글 세트
    place    = (data.get('place') or '').strip()[:60]
    visit_at = (data.get('visit_at') or '').strip()[:40]

    # 포인트 확인·차감 준비 — 사진 없으면(텍스트 전용, vision 미사용) 할인,
    # 네이버+구글 세트는 2배 (구글판이 더 길고 깊어 출력 토큰도 ~2배)
    base_cost = POINT_COSTS.get('experience_blog', 150) if photos else 100
    cost = base_cost * 2 if both else base_cost
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
    if photos and not images:
        return jsonify(ok=False, message='사진 형식을 읽을 수 없습니다. 다시 업로드해 주세요.')

    type_label, structure = _TYPE_GUIDES[exp_type]
    photo_rule = (
        '- 각 사진이 들어갈 자리에 [사진 N] 마커를 한 줄로 단독 배치한다. '
        '모든 사진을 순서대로 1회씩 사용한다.\n'
        if images else
        '- 사진이 없다. 대신 스크린샷/사진을 넣으면 좋을 자리 2~4곳에 '
        '(📷 여기에 ○○ 스크린샷/사진 추천) 형태의 힌트를 한 줄로 넣는다 '
        '(예: 매출 그래프, 광고 관리 화면, 실제 상품 사진 — 민감정보 모자이크 권장).\n'
    )
    system_prompt = (
        '당신은 네이버 블로그 경험담 전문 에디터입니다. 사용자가 '
        + ('올린 실제 사진들과 ' if images else '')
        + '대충 쓴 메모를 바탕으로, 직접 겪은 사람의 목소리로 자연스러운 경험담 글을 씁니다.\n'
        '\n'
        '절대 규칙:\n'
        '- ' + ('사진에서 보이는 것과 ' if images else '') + '메모에 적힌 것만 쓴다. '
        '확인할 수 없는 사실(가격·수치·지명 등)은 창작하지 않는다. 메모에 없으면 그냥 언급하지 않는다.\n'
        '- [시점] 글쓴이 본인이 직접 겪은 일을 1인칭("나/저")으로 쓴다. 동행한 가족은 화자의 '
        '가족으로 부른다: 아이는 "우리 아이/아들/딸", 배우자는 "남편/아내". 절대 제3자·관찰자 '
        '시점(예: "한 아이가", "아이가 즐거워 보였다"처럼 남을 구경하듯)으로 쓰지 않는다.\n'
        '- [문체] 아래 지정된 문체와 문장 어미를 글 전체에서 처음부터 끝까지 일관되게 유지한다. '
        '중간에 어미나 톤이 바뀌지 않게 한다.\n'
        '- [자연스러움] AI 티 나는 상투구·과장을 쓰지 않는다: "여러분", "정말 최고", "강력 추천", '
        '"많은 분들께 도움이 되길", 근거 없는 일반화, 접속어("또한/뿐만 아니라") 남발, '
        '광고 문구("인생 맛집", "무조건 가세요") 모두 금지. 메모에 있는 구체적 장면·디테일로 솔직하게 채운다.\n'
        + photo_rule +
        '- 네이버 블로그 스타일: 2~4문장짜리 짧은 문단, 문단 사이 빈 줄, 소제목 활용.\n'
        + ('- 네이버판은 마크다운 기호 없이 일반 텍스트만(에디터 복붙용), '
           '구글판은 마크다운 소제목(##)을 사용한다.'
           if both else
           '- 마크다운 기호(#, **, -) 대신 일반 텍스트와 줄바꿈만 사용한다(네이버 에디터 복붙용).')
    )
    _len_label, naver_len, google_len, len_tokens = _LENGTHS[length]
    naver_format = (
        f'분량 {naver_len}(공백 포함). '
        '제목 후보 3개(각 25자 내외, 검색어가 앞에 오게) → 빈 줄 → 본문'
        + ('(사진 마커 포함)' if images else '(스크린샷 추천 힌트 포함)')
        + ' → 마지막에 해시태그 8~12개(#태그 형식 한 줄). '
          '마크다운 기호 없이 일반 텍스트만. 후기 스타일, 짧은 문단.'
    )
    google_format = (
        f'워드프레스(구글 검색용) 판. 분량 {google_len}(공백 포함) — 네이버판보다 확실히 '
        '길고 깊게, 배경 설명·상세 팁을 보강한다. 마크다운 사용. 순서:\n'
        '  SEO 제목: (60자 이내, 핵심 검색어 앞배치)\n'
        '  메타 설명: (150자 이내)\n'
        '  슬러그: (영문 소문자-하이픈)\n'
        '  본문: ## / ### 소제목으로 구조화. 비교·정리 가능한 정보(비용·준비물·장단점·'
        '소요시간 등)는 마크다운 표 1개 이상으로 정리한다(메모·사진에서 확인되는 정보만). '
        '사진 마커는 동일하게 [사진 N]을 쓰되 각 마커 다음 줄에 "알트텍스트: ..." 제안을 '
        '붙인다.\n'
        '  FAQ: 독자가 검색할 질문 3~5개를 ### 질문 + 답변으로.\n'
        '  태그: 쉼표로 구분한 키워드 8~10개.\n'
        '  중요: 주제는 같아도 도입·소제목 구성·문장을 네이버판과 30~50% 이상 다르게 쓴다. '
        '네이버판 문장을 그대로 재사용하지 않는다(검색엔진 중복 콘텐츠 회피).'
    )
    if both:
        output_rule = (
            '아래 두 가지 판을 모두 작성하세요. 반드시 이 구분자를 그대로 사용:\n'
            '[[[NAVER]]]\n(네이버판 — ' + naver_format + ')\n'
            '[[[GOOGLE]]]\n(' + google_format + ')'
        )
    else:
        output_rule = '출력 형식:\n' + naver_format
    user_prompt = (
        f'글 유형: {type_label}\n'
        f'권장 구성: {structure}\n'
        f'지정 문체(글 전체에서 반드시 일관 유지): {_TONES[tone]}\n'
        '분량 공통 원칙: 메모가 짧아 채울 내용이 없으면 억지로 늘리지 말고 자연스러운 '
        '선에서 마무리\n'
        + (f'장소/제품명: {place}\n' if place else '')
        + (f'시기: {visit_at}\n' if visit_at else '')
        + '\n[내 메모 — 이 경험이 글의 재료입니다]\n'
        + memo[:2000]
        + '\n\n'
        + ('위 사진들을 순서대로 살펴보고, 사진 내용과 메모를 엮어 '
           if images else '위 메모의 경험을 살려 ')
        + '경험담 블로그 글을 작성하세요.\n'
        + output_rule
    )
    if both:
        len_tokens = int(len_tokens * 2.2)   # 구글판(더 긴 분량) 출력 여유

    # ── 포인트 선차감 → creations(generating) 기록 → 워커 제출 ──
    # (동기 생성은 메인 서버를 20~90초 블로킹하므로 Celery 워커로 이전.
    #  실패 시 워커가 포인트를 자동 환불한다 — tasks/experience_task.py)
    cid = str(_uuid.uuid4())
    try:
        use_points(current_user, 'experience_blog', cid, cost_override=cost)
    except InsufficientPoints as e:
        return jsonify(ok=False, message=str(e))
    except Exception as e:
        logger.error(f'[experience] 포인트 차감 실패: {e}', exc_info=True)
        return jsonify(ok=False, message='포인트 차감 중 오류가 발생했습니다.')

    try:
        if current_app.supabase:
            now_s = now_kst().isoformat()
            row = {
                'id': cid, 'user_id': current_user.id,
                'creation_type': 'experience_blog',
                'input_data': {'exp_type': exp_type, 'tone': tone, 'length': length,
                               'targets': 'both' if both else 'naver',
                               'place': place, 'visit_at': visit_at, 'memo': memo[:500],
                               'photo_count': len(images)},
                'output_data': {}, 'points_used': cost, 'status': 'generating',
                'created_at': now_s, 'updated_at': now_s,
            }
            if getattr(current_user, 'operator_id', None):
                row['operator_id'] = current_user.operator_id
            current_app.supabase.table('creations').insert(row).execute()
    except Exception as e:
        logger.warning(f'[experience] creations insert 실패: {e}')

    # Celery 워커에 제출
    import os
    from services.config_service import get_config
    from tasks.experience_task import generate_experience as _gen_task

    supabase_url  = os.environ.get('SUPABASE_URL', '')
    supabase_key  = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY', '')
    anthropic_key = get_config('anthropic_api_key') or os.environ.get('ANTHROPIC_API_KEY', '')

    _gen_task.delay(
        creation_id=cid,
        user_id=current_user.id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        images=[[b64, mt] for (b64, mt) in images],
        max_tokens=len_tokens,
        both=both,
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        anthropic_api_key=anthropic_key,
    )

    logger.info(f'[experience] 제출 uid={str(current_user.id)[:8]} cid={cid[:8]} '
                f'type={exp_type} photos={len(images)} '
                f'targets={"both" if both else "naver"} cost={cost}P')
    return jsonify(ok=True, id=cid, async_mode=True, cost=cost)


@create_bp.route('/experience/status/<cid>', methods=['GET'])
@login_required
def experience_status(cid):
    """경험담 생성 Celery 태스크 완료 여부 폴링."""
    supabase = current_app.supabase
    if not supabase:
        return jsonify(ok=False, status='error', message='DB 연결이 없습니다.')
    try:
        r = supabase.table('creations').select(
            'id, status, output_data, user_id'
        ).eq('id', cid).single().execute()
        row = r.data
    except Exception:
        return jsonify(ok=False, status='error', message='조회 실패')

    if not row or row.get('user_id') != current_user.id:
        return jsonify(ok=False, status='error', message='권한 없음')

    status = row.get('status', '')
    if status == 'done':
        od = row.get('output_data') or {}
        return jsonify(ok=True, status='done',
                       text=od.get('text', ''), google_text=od.get('google_text', ''))
    elif status == 'failed':
        od = row.get('output_data') or {}
        return jsonify(ok=False, status='failed',
                       message=(od.get('error')
                                or '글 생성 중 오류가 발생했습니다.') + ' (포인트는 환불되었습니다)')
    else:
        return jsonify(ok=True, status='generating')
