"""쿠파스 스튜디오 — 소스 영상 가져오기 라우트 (Phase 1: 영상 가져오기)

원본(1688 상품영상 URL 또는 업로드 파일)을 워커에 넘겨 무음화 후 Supabase Storage 에 저장.
- URL:    워커가 m.1688.com 에서 추출·다운로드 (타오바오/티몰=로그인벽, 더우인=Phase 2)
- 업로드: 원본을 Storage 임시경로에 올린 뒤 워커가 내려받아 처리
무료 단계(포인트 미차감). 결과는 폴링으로 조회.
"""
import logging
import uuid

from flask import render_template, request, jsonify, current_app
from flask_login import login_required, current_user

from blueprints.create import create_bp
from services.tz_utils import now_kst

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024   # 200MB
_ALLOWED_EXT = ('mp4', 'mov', 'webm', 'mkv', 'avi', 'm4v')


def _supabase_creds():
    url = current_app.config.get('SUPABASE_URL', '')
    key = (current_app.config.get('SUPABASE_SERVICE_KEY')
           or current_app.config.get('SUPABASE_KEY', ''))
    return url, key


@create_bp.route('/coupas')
@login_required
def coupas():
    return render_template('create/coupas.html')


@create_bp.route('/coupas/import', methods=['POST'])
@login_required
def coupas_import():
    """URL 또는 업로드 파일을 받아 워커 태스크 제출 → creation_id 반환."""
    # 전역 MAX_CONTENT_LENGTH(10MB)를 이 라우트에서만 상향 — 영상 업로드용.
    # request.files 접근(폼 파싱) 전에 설정해야 적용됨 (Flask 3.1+ settable).
    request.max_content_length = MAX_UPLOAD_BYTES

    supabase = current_app.supabase
    creation_id = str(uuid.uuid4())
    su, sk = _supabase_creds()

    file = request.files.get('video')
    source_url = None
    raw_storage_path = None

    if file and file.filename:
        data = file.read()
        if len(data) < 1024:
            return jsonify(ok=False, message='영상 파일이 비어있습니다.')
        if len(data) > MAX_UPLOAD_BYTES:
            return jsonify(ok=False,
                           message=f'영상이 너무 큽니다 (최대 {MAX_UPLOAD_BYTES // 1024 // 1024}MB).')
        ext = 'mp4'
        if '.' in file.filename:
            cand = file.filename.rsplit('.', 1)[-1].lower()
            if cand in _ALLOWED_EXT:
                ext = cand
        raw_storage_path = f'coupas/source/{current_user.id}/{creation_id}_raw.{ext}'
        try:
            supabase.storage.from_('creations').upload(
                raw_storage_path, data,
                file_options={'content-type': file.mimetype or 'video/mp4',
                              'upsert': 'true'})
        except Exception as e:
            logger.error('[coupas_import] 업로드 실패: %s', e)
            return jsonify(ok=False, message=f'업로드 실패: {e}')
        input_data = {'mode': 'upload', 'filename': file.filename}
    else:
        body = request.get_json(silent=True) or {}
        source_url = (body.get('url') or '').strip()
        if not source_url:
            return jsonify(ok=False, message='상품 URL 또는 영상 파일이 필요합니다.')
        input_data = {'mode': 'url', 'source_url': source_url}

    try:
        row = {
            'id': creation_id, 'user_id': current_user.id,
            'creation_type': 'coupas_import',
            'input_data': input_data,
            'output_data': {'step': '영상을 가져오는 중'},
            'points_used': 0, 'status': 'generating',
            'model_used': 'video_import', 'created_at': now_kst().isoformat(),
        }
        if getattr(current_user, 'operator_id', None):
            row['operator_id'] = current_user.operator_id
        supabase.table('creations').insert(row).execute()
    except Exception as e:
        logger.warning('[coupas_import] creations insert: %s', e)

    from tasks.coupas_task import import_source_video
    import_source_video.delay(
        creation_id=creation_id, user_id=current_user.id,
        source_url=source_url, raw_storage_path=raw_storage_path,
        supabase_url=su, supabase_key=sk)

    return jsonify(ok=True, creation_id=creation_id)


@create_bp.route('/coupas/script', methods=['POST'])
@login_required
def coupas_script():
    """상품명+셀링포인트 → 영상 길이에 맞춘 홍보 멘트 세그먼트 + 쿠팡 캡션 초안.

    무료(포인트 미차감). 세그먼트는 자막 한 줄 = TTS 한 조각 단위.
    실제 타이밍은 이후 TTS 단계에서 각 세그먼트 음성 길이로 확정.
    """
    import json
    import re as _re

    data = request.get_json(silent=True) or {}
    product_name = (data.get('product_name') or '').strip()
    selling_points = (data.get('selling_points') or '').strip()
    try:
        duration = float(data.get('duration') or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not product_name:
        return jsonify(ok=False, message='상품명을 입력하세요.')

    # 한국어 나레이션 대략 4.5자/초 → 세그먼트 수 가늠 (영상 길이 모르면 20초 가정)
    dur = duration if duration and duration > 3 else 20.0
    seg_count = max(3, min(8, round(dur / 5)))   # 5초당 1세그먼트, 3~8개

    from services.claude_service import generate_text

    system = (
        '당신은 쿠팡 파트너스용 숏폼 상품 홍보 영상의 나레이션 작가입니다. '
        '스크롤을 멈추게 하는 강한 훅으로 시작해, 상품의 매력을 빠르고 친근한 구어체로 전달하고, '
        '마지막에 쿠팡에서 확인하도록 자연스럽게 유도합니다. '
        '\n[규칙]\n'
        '• 한국어 구어체, 각 문장은 자막 한 줄로 읽기 좋게 12~22자.\n'
        '• 과장·허위 광고 표현 금지(최고/1위/100% 등 근거 없는 단정 회피). 체감·감성 위주.\n'
        '• 의료·효능 단정 금지. 가격/할인 언급은 하지 않음(변동되므로).\n'
        '• 첫 세그먼트=훅, 마지막=CTA(쿠팡 유도), 중간=핵심 매력.\n'
        '순수 JSON만 출력.'
    )
    prompt = f"""아래 상품으로 {dur:.0f}초 분량 숏폼 홍보 나레이션을 만드세요.

[상품명]
{product_name}

[핵심 셀링포인트 / 참고]
{selling_points or '(입력 없음 — 상품명과 영상 분위기로 추론)'}

[분량] 세그먼트 {seg_count}개 (첫=훅, 끝=CTA)

[출력 — 순수 JSON]
{{
  "segments": [
    {{"role": "hook",  "text": "스크롤 멈추는 첫 마디"}},
    {{"role": "body",  "text": "핵심 매력 한 줄"}},
    {{"role": "cta",   "text": "지금 쿠팡에서 확인해보세요 같은 유도"}}
  ],
  "caption": "인스타/릴스 캡션 초안 (해시태그 5개 포함, 링크는 [쿠팡링크]로 표시)"
}}
세그먼트는 정확히 {seg_count}개. 순수 JSON만 출력."""

    try:
        raw = generate_text(system, prompt, max_tokens=1200)
        clean = _re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=_re.MULTILINE).strip()
        s, e = clean.find('{'), clean.rfind('}') + 1
        if s >= 0 and e > s:
            clean = clean[s:e]
        parsed = json.loads(clean)
        segments = parsed.get('segments') or []
        caption = parsed.get('caption') or ''
        # 쿠팡 파트너스 의무 고지 문구 자동 첨부 (없으면)
        notice = '이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.'
        if caption and notice[:20] not in caption:
            caption = f'{caption}\n\n{notice}'
        return jsonify(ok=True, segments=segments[:8], caption=caption)
    except Exception as e:
        logger.error('[coupas/script] %s', e)
        return jsonify(ok=False, message=f'멘트 생성 실패: {str(e)[:200]}')


@create_bp.route('/coupas/import/status/<creation_id>', methods=['GET'])
@login_required
def coupas_import_status(creation_id):
    supabase = current_app.supabase
    r = supabase.table('creations').select('status,output_data').eq(
        'id', creation_id).eq('user_id', current_user.id).execute()
    if not r.data:
        return jsonify(ok=False, message='없는 작업입니다.')
    row = r.data[0]
    return jsonify(ok=True, status=row['status'],
                   output_data=row.get('output_data') or {})
