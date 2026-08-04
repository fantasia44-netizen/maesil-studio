"""쿠파스 스튜디오 — 소스 영상 가져오기 Celery 태스크

원본(중국 상품) 영상을 워커에서 가져와 무음화 후 Supabase Storage 에 저장.
웹 요청을 블로킹하지 않으며, 데이터센터 IP/디스크 부담을 웹 서버와 분리한다.

입력 두 경로:
  - source_url:        1688/타오바오 상품페이지 또는 .mp4 직링크 → 워커가 다운로드
  - raw_storage_path:  사용자가 업로드해 Storage 임시경로에 올려둔 원본 → 워커가 내려받아 처리

산출:
  creations(status=done).output_data = {
    video_url, storage_path, platform, source_url, meta{duration,width,height,...}
  }
무료 단계(포인트 미차감)이므로 실패 시 환불 없이 status=failed 만 기록.
"""
import logging
import os
import tempfile
import uuid

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, name='tasks.coupas_task.import_source_video',
             max_retries=0, soft_time_limit=240, time_limit=300)
def import_source_video(self, creation_id, user_id,
                        source_url=None, raw_storage_path=None,
                        supabase_url='', supabase_key=''):
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    from services import video_import as vi

    supabase = create_client(supabase_url, supabase_key)
    tmp_dir = None
    local_path = None
    muted_path = None
    platform = 'upload'

    try:
        # 1) 원본 확보 — URL 다운로드 또는 Storage 원본 내려받기
        if source_url:
            platform = vi.detect_source_platform(source_url)
            result = vi.import_from_url(source_url)
            if not result.get('ok'):
                raise RuntimeError(result.get('error') or '영상을 가져오지 못했습니다.')
            local_path = result['local_path']
            tmp_dir = os.path.dirname(local_path)
            meta = result.get('meta') or {}
            resolved_url = result.get('source_url')
        elif raw_storage_path:
            tmp_dir = tempfile.mkdtemp(prefix='coupas_')
            local_path = os.path.join(tmp_dir, f'{uuid.uuid4().hex[:10]}.mp4')
            data = supabase.storage.from_(vi.STORAGE_BUCKET).download(raw_storage_path)
            with open(local_path, 'wb') as f:
                f.write(data)
            meta = vi.probe_video(local_path)
            resolved_url = None
        else:
            raise ValueError('source_url 또는 raw_storage_path 가 필요합니다.')

        # 2) 오디오 제거 (무음화)
        muted_path = os.path.join(tmp_dir, f'{uuid.uuid4().hex[:10]}_mute.mp4')
        vi.strip_audio(local_path, muted_path)
        muted_meta = vi.probe_video(muted_path)

        # 3) Storage 업로드 (정리 잡이 스캔하는 규칙 경로)
        dest_path = vi.source_storage_path(user_id, creation_id)
        video_url = vi.upload_file(supabase, muted_path, dest_path)

        # 4) 업로드 원본(raw)은 처리 후 즉시 제거 — 스토리지 절약
        if raw_storage_path:
            vi.delete_storage_paths(supabase, [raw_storage_path])

        out_meta = {
            'duration': muted_meta.get('duration') or meta.get('duration'),
            'width': muted_meta.get('width') or meta.get('width'),
            'height': muted_meta.get('height') or meta.get('height'),
            'size_bytes': muted_meta.get('size_bytes'),
            'had_audio': meta.get('has_audio'),
        }
        supabase.table('creations').update({
            'status': 'done',
            'output_data': {
                'video_url': video_url,
                'storage_path': dest_path,
                'platform': platform,
                'source_url': resolved_url,
                'meta': out_meta,
                'source_deleted': False,
            },
        }).eq('id', creation_id).execute()
        logger.info('[coupas_task] import 완료 cid=%s platform=%s dur=%s',
                    creation_id, platform, out_meta.get('duration'))

    except Exception as e:
        logger.error('[coupas_task] import 오류 cid=%s: %s', creation_id, e, exc_info=True)
        try:
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {'error': str(e)[:300]},
            }).eq('id', creation_id).execute()
        except Exception:
            pass
        raise
    finally:
        if tmp_dir:
            vi._safe_rmtree(tmp_dir)
