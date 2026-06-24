"""Celery 앱 — 쇼츠 영상 생성 등 CPU/시간 집약 작업을 별도 워커에서 처리"""
import os
import signal
import logging
from celery import Celery
from celery.signals import worker_shutdown, celeryd_init

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

celery = Celery(
    'maesil',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks.shorts_task', 'tasks.banner_task', 'tasks.detail_page_task'],
)

celery.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='Asia/Seoul',
    enable_utc=True,
    # ── 동시성: 3명 동시 처리, 이후 Redis 큐 대기 ──────────────
    worker_concurrency=3,                  # 3개 슬롯 동시 처리
    worker_prefetch_multiplier=1,          # 슬롯당 1개만 미리 가져옴 (공정 대기열)
    # ── 메모리 관리 (서버 2GB 기준) ──────────────────────────────
    worker_max_tasks_per_child=10,         # 10개 처리 후 자식 프로세스 재시작 (메모리 누수 방지)
    worker_max_memory_per_child=550000,    # 자식 1개당 550MB 초과 시 재시작 (3개 × 550MB = 1.65GB)
    # ── 타임아웃 ─────────────────────────────────────────────────
    task_soft_time_limit=600,              # 10분 소프트 타임아웃 (SoftTimeLimitExceeded 예외)
    task_time_limit=720,                   # 12분 하드 타임아웃 (강제 종료)
    # ── 신뢰성 ───────────────────────────────────────────────────
    task_acks_late=True,                   # 작업 완료 후 ACK → 워커 crash 시 자동 재큐
    task_reject_on_worker_lost=True,       # 워커 유실 시 큐로 복귀
    broker_connection_retry_on_startup=True,  # 브로커 재시작 시 자동 재연결
    # ── 좀비 프로세스 방지 ─────────────────────────────────────
    worker_cancel_long_running_tasks_on_connection_loss=True,  # 연결 끊기면 장기 태스크 취소
)


# ── 워커 종료 시 고아 FFmpeg 프로세스 정리 ──────────────────────
@worker_shutdown.connect
def cleanup_zombie_ffmpeg(sender, **kwargs):
    """워커 SIGTERM 시 추적 중인 FFmpeg 프로세스 그룹 강제 종료."""
    from services.shorts_service import _kill_all_tracked_procs
    try:
        _kill_all_tracked_procs()
        logger.info('[celery] 워커 종료 — FFmpeg 프로세스 정리 완료')
    except Exception as e:
        logger.warning('[celery] FFmpeg 정리 오류: %s', e)


@celeryd_init.connect
def configure_worker(sender, conf, **kwargs):
    """워커 초기화 시 SIGTERM 핸들러 등록."""
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _sigterm_handler(signum, frame):
        logger.info('[celery] SIGTERM 수신 — FFmpeg 자식 프로세스 정리')
        from services.shorts_service import _kill_all_tracked_procs
        try:
            _kill_all_tracked_procs()
        except Exception:
            pass
        if callable(original_sigterm):
            original_sigterm(signum, frame)

    signal.signal(signal.SIGTERM, _sigterm_handler)
