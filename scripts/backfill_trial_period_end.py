"""trial 구독 중 current_period_end 없는 행을 created_at + 30일로 백필."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import timedelta
from datetime import datetime, timezone
from supabase import create_client

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY')
if not url or not key:
    from dotenv import load_dotenv
    load_dotenv()
    url = os.environ['SUPABASE_URL']
    key = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']

sb = create_client(url, key)

rows = sb.table('subscriptions').select(
    'id, user_id, status, created_at, current_period_end'
).eq('status', 'trial').is_('current_period_end', 'null').execute().data or []

print(f'백필 대상: {len(rows)}건')
updated = 0
for r in rows:
    created = r.get('created_at', '')
    if not created:
        print(f'  SKIP {r["id"]} — created_at 없음')
        continue
    try:
        dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
        end = (dt + timedelta(days=30)).isoformat()
        sb.table('subscriptions').update({
            'current_period_end':   end,
            'current_period_start': created,
        }).eq('id', r['id']).execute()
        print(f'  OK {r["user_id"][:8]}… → {end[:10]}')
        updated += 1
    except Exception as e:
        print(f'  ERR {r["id"]}: {e}')

print(f'\n완료: {updated}/{len(rows)}건 업데이트')
