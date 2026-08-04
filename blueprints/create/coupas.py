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
            supabase.storage.from_('maesil-files').upload(
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
