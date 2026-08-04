"""쿠파스 스튜디오 — 소스 영상 가져오기 Celery 태스크

원본(중국 상품) 영상을 워커에서 가져와 무음화 후 Supabase Storage 에 저장.
웹 요청을 블로킹하지 않으며, 데이터센터 IP/디스크 부담을 웹 서버와 분리한다.

입력 두 경로:
  - source_url:        직접 영상 링크(cloud.video.taobao.com/....mp4, 우클릭→동영상 주소 복사)
                       또는 1688 상품페이지 URL(자동추출, 안티봇으로 실패 가능)
  - raw_storage_path:  사용자가 업로드해 Storage 임시경로에 올려둔 원본

산출:
  creations(status=done).output_data = {video_url, storage_path, platform, source_url, meta}
무료 단계(포인트 미차감)이므로 실패 시 환불 없이 status=failed + 상세 에러 기록.
"""
import logging
import os
import tempfile
import traceback
import uuid

from celery_app import celery

logger = logging.getLogger(__name__)


def _set_step(supabase, creation_id, step):
    """진행 단계를 output_data.step 에 기록 (폴링 UI에 실시간 표시)."""
    try:
        supabase.table('creations').update(
            {'output_data': {'step': step}}).eq('id', creation_id).execute()
    except Exception:
        pass


def _fail(supabase, creation_id, step, e):
    """실패 기록 — 에러를 절대 비우지 않음(SoftTimeLimit 등 대응)."""
    import traceback
    if 'SoftTimeLimit' in type(e).__name__:
        err = f'시간 초과 — [{step}] 단계에서 지연되었습니다.'
    else:
        err = f'[{step}] {str(e).strip() or repr(e) or type(e).__name__}'
    logger.error('[coupas_task] 오류 cid=%s: %s\n%s', creation_id, err, traceback.format_exc())
    try:
        supabase.table('creations').update({
            'status': 'failed',
            'output_data': {'error': err[:300], 'step': step,
                            'trace': traceback.format_exc()[-600:]},
        }).eq('id', creation_id).execute()
    except Exception:
        pass


@celery.task(bind=True, name='tasks.coupas_task.render_narrated_video',
             max_retries=0, soft_time_limit=300, time_limit=360)
def render_narrated_video(self, creation_id, user_id, video_url, segments,
                          voice_key='female_natural', tts_speed=1.05,
                          supabase_url='', supabase_key=''):
    """무음 영상 + 멘트 → TTS 음성 + 타임라인 자막 합성 → 최종 MP4 (B·C단계)."""
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from supabase import create_client
    from services import coupas_render as cr, video_import as vi
    from services.config_service import get_config

    supabase = create_client(supabase_url, supabase_key)
    tmp_dir = None
    step = '시작'
    try:
        step = '음성 준비 중'
        _set_step(supabase, creation_id, step)
        tts_key = get_config('google_tts_api_key')
        if not tts_key:
            raise RuntimeError('google_tts_api_key가 설정되지 않았습니다. 시스템 설정에서 등록하세요.')

        tmp_dir = tempfile.mkdtemp(prefix='coupasrender_')
        muted = os.path.join(tmp_dir, 'muted.mp4')
        step = '영상 불러오는 중'
        _set_step(supabase, creation_id, step)
        cr.download(video_url, muted)

        step = '음성·자막 합성 중'
        _set_step(supabase, creation_id, step)
        out = os.path.join(tmp_dir, 'out.mp4')
        meta = cr.render_narration(muted, segments, voice_key, float(tts_speed),
                                   tts_key, out, tmp_dir)

        step = '저장 중'
        _set_step(supabase, creation_id, step)
        dest = f'coupas/rendered/{user_id}/{creation_id}.mp4'
        final_url = vi.upload_file(supabase, out, dest)

        supabase.table('creations').update({
            'status': 'done',
            'output_data': {'video_url': final_url, 'storage_path': dest,
                            'meta': meta, 'source_deleted': False},
        }).eq('id', creation_id).execute()
        logger.info('[coupas_task] render 완료 cid=%s dur=%s', creation_id, meta.get('duration'))
    except Exception as e:
        _fail(supabase, creation_id, step, e)
        raise
    finally:
        if tmp_dir:
            from services import video_import as _vi
            _vi._safe_rmtree(tmp_dir)


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
    step = '시작'

    try:
        # 1) 원본 확보
        if source_url:
            platform = vi.detect_source_platform(source_url)
            step = '영상 다운로드 중'
            _set_step(supabase, creation_id, step)
            result = vi.import_from_url(source_url)
            if not result.get('ok'):
                raise RuntimeError(result.get('error') or '영상을 가져오지 못했습니다.')
            local_path = result['local_path']
            tmp_dir = os.path.dirname(local_path)
            meta = result.get('meta') or {}
            resolved_url = result.get('source_url')
        elif raw_storage_path:
            step = '업로드 원본 불러오는 중'
            _set_step(supabase, creation_id, step)
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
        step = '소리 제거 중'
        _set_step(supabase, creation_id, step)
        muted_path = os.path.join(tmp_dir, f'{uuid.uuid4().hex[:10]}_mute.mp4')
        vi.strip_audio(local_path, muted_path)
        muted_meta = vi.probe_video(muted_path)

        # 3) Storage 업로드
        step = '저장 중'
        _set_step(supabase, creation_id, step)
        dest_path = vi.source_storage_path(user_id, creation_id)
        video_url = vi.upload_file(supabase, muted_path, dest_path)

        # 4) 업로드 원본(raw) 즉시 제거 — 스토리지 절약
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
        # 에러를 절대 비우지 않음 — SoftTimeLimitExceeded 등 str()이 빈 예외 대응
        is_timeout = 'SoftTimeLimit' in type(e).__name__
        if is_timeout:
            err = f'시간 초과(240초) — [{step}] 단계에서 지연. Render IP가 CDN 다운로드에 느릴 수 있습니다.'
        else:
            base = str(e).strip() or repr(e) or type(e).__name__
            err = f'[{step}] {base}'
        logger.error('[coupas_task] import 오류 cid=%s: %s\n%s',
                     creation_id, err, traceback.format_exc())
        try:
            supabase.table('creations').update({
                'status': 'failed',
                'output_data': {
                    'error': err[:300],
                    'step': step,
                    'trace': traceback.format_exc()[-600:],
                },
            }).eq('id', creation_id).execute()
        except Exception:
            pass
        raise
    finally:
        if tmp_dir:
            vi._safe_rmtree(tmp_dir)
