"""Celery 앱 — 쇼츠 영상 생성 등 CPU/시간 집약 작업을 별도 워커에서 처리"""
import os
from celery import Celery

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

celery = Celery(
    'maesil',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks.shorts_task'],
)

celery.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='Asia/Seoul',
    enable_utc=True,
    worker_max_tasks_per_child=5,          # 5개 처리 후 워커 재시작 (메모리 누수 방지)
    worker_max_memory_per_child=1500000,  # 1.5GB 초과 시 워커 자동 재시작 (KB 단위, 서버 2GB 기준)
    worker_concurrency=1,                # 영상 생성은 한 번에 하나씩 (CPU 보호)
    task_soft_time_limit=600,            # 10분 소프트 타임아웃 (SoftTimeLimitExceeded)
    task_time_limit=720,                 # 12분 하드 타임아웃 (강제 종료)
    task_acks_late=True,                 # 작업 완료 후 ACK → 워커 crash 시 재큐
    task_reject_on_worker_lost=True,     # 워커 유실 시 재시도 큐로 복귀
    broker_connection_retry_on_startup=True,  # 브로커 재시작 시 자동 재연결
)
