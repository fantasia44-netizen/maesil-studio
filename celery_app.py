"""Celery 앱 — 쇼츠 영상 생성 등 CPU/시간 집약 작업을 별도 워커에서 처리"""
import os
from celery import Celery

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

celery = Celery(
    'maesil',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks.shorts_task', 'tasks.banner_task'],
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
)
