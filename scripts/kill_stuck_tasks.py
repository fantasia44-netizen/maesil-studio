"""
stuck 상태('generating')로 멈춘 creation 레코드를 'failed'로 일괄 업데이트.

Render 쉘 또는 로컬에서 실행:
  python scripts/kill_stuck_tasks.py

환경변수 필요:
  SUPABASE_URL, SUPABASE_SERVICE_KEY (또는 SUPABASE_KEY)
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    print('ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다.')
    sys.exit(1)

from supabase import create_client
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# 현재 stuck 목록 조회
r = sb.table('creations').select('id,creation_type,created_at,user_id') \
      .eq('status', 'generating').execute()

rows = r.data or []
if not rows:
    print('멈춘 작업이 없습니다.')
    sys.exit(0)

print(f'멈춘 작업 {len(rows)}건:')
for row in rows:
    print(f"  [{row['creation_type']}] {row['id']} / {row.get('created_at','?')}")

if '--force' not in sys.argv:
    confirm = input(f'\n위 {len(rows)}건을 failed 처리하겠습니까? (y/N): ').strip().lower()
    if confirm != 'y':
        print('취소.')
        sys.exit(0)

# 일괄 failed 처리
ids = [r['id'] for r in rows]
for cid in ids:
    sb.table('creations').update({
        'status': 'failed',
        'output_data': {'error': '서버 재시작으로 인해 중단됨', 'progress': 0}
    }).eq('id', cid).execute()

print(f'완료: {len(ids)}건 → failed 처리됨.')
