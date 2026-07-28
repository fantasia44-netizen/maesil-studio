"""search_console — 구글 Search Console 성과 동기화 + 리라이트 제안.

두 부분으로 분리:
  1) sync_performance: GSC API로 페이지별 노출/클릭/순위를 가져와
     published_content_index.sc_* 갱신. (구글 라이브러리 + 서비스계정 필요 —
     없으면 안내 메시지 반환, 앱은 정상)
  2) rewrite_suggestions: 저장된 sc_* 데이터로 개선 제안 생성(API 불필요, 규칙 기반).

설정(활성화 시):
  - pip install google-api-python-client google-auth
  - saas_config 'gsc_service_account_json' = 서비스계정 키(JSON 문자열)
  - GSC 속성(sc-domain:maesil.net 등)에 해당 서비스계정 이메일을 사용자로 추가
  - saas_config 'gsc_site_url' = 'sc-domain:maesil.net' 또는 'https://blog.maesil.net/'
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _service():
    """GSC API 서비스 객체. 의존성/설정 없으면 (None, 사유)."""
    from services.config_service import get_config
    sa_json = get_config('gsc_service_account_json')
    if not sa_json:
        return None, 'gsc_service_account_json 미설정'
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception:
        return None, 'google-api-python-client/google-auth 미설치 (pip install 필요)'
    try:
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/webmasters.readonly'])
        return build('searchconsole', 'v1', credentials=creds, cache_discovery=False), None
    except Exception as e:
        return None, f'서비스계정 인증 실패: {e}'


def sync_performance(supabase, *, days: int = 28, row_limit: int = 1000) -> dict:
    """GSC 페이지별 성과 → published_content_index.sc_* 갱신(URL 매칭)."""
    from services.config_service import get_config
    from datetime import date, timedelta

    svc, err = _service()
    if not svc:
        return {'ok': False, 'error': err}
    site = get_config('gsc_site_url')
    if not site:
        return {'ok': False, 'error': 'gsc_site_url 미설정'}

    end = date.today()
    start = end - timedelta(days=days)
    try:
        resp = svc.searchanalytics().query(siteUrl=site, body={
            'startDate': start.isoformat(), 'endDate': end.isoformat(),
            'dimensions': ['page'], 'rowLimit': row_limit,
        }).execute()
    except Exception as e:
        logger.warning('[GSC] query 실패: %s', e)
        return {'ok': False, 'error': f'GSC 조회 실패: {e}'}

    updated = 0
    for row in resp.get('rows', []):
        url = (row.get('keys') or [''])[0]
        if not url:
            continue
        payload = {
            'sc_impressions': int(row.get('impressions') or 0),
            'sc_clicks': int(row.get('clicks') or 0),
            'sc_avg_position': round(float(row.get('position') or 0), 1),
        }
        try:
            supabase.table('published_content_index').update(payload).eq('url', url).execute()
            updated += 1
        except Exception as e:
            logger.debug('[GSC] update 실패 %s: %s', url, e)
    return {'ok': True, 'rows': len(resp.get('rows', [])), 'updated': updated}


def rewrite_suggestions(supabase, *, brand_id: str | None = None,
                        min_impressions: int = 100) -> list[dict]:
    """저장된 sc_* 데이터로 페이지별 개선 제안(규칙 기반, API 불필요).

    - 노출 충분+평균순위 8~20위: 제목/FAQ/내부링크 보강 시 1페이지 진입 여지 큼
    - 노출 충분+CTR 낮음: 제목·메타 개선
    - 노출 100 미만: 데이터 부족(제안 보류)
    """
    try:
        q = supabase.table('published_content_index').select(
            'title, url, sc_impressions, sc_clicks, sc_avg_position')
        if brand_id:
            q = q.eq('brand_id', brand_id)
        rows = q.limit(500).execute().data or []
    except Exception as e:
        logger.warning('[GSC] 인덱스 조회 실패: %s', e)
        return []

    out = []
    for r in rows:
        imp = r.get('sc_impressions') or 0
        clk = r.get('sc_clicks') or 0
        pos = r.get('sc_avg_position') or 0
        if imp < min_impressions or not pos:
            continue
        ctr = (clk / imp) if imp else 0
        tips = []
        if 8 <= pos <= 20:
            tips.append(f'평균 {pos}위 — 제목 앞부분에 핵심 검색어 배치 + FAQ/내부링크 보강 시 1페이지 진입 여지')
        if imp >= 300 and ctr < 0.02:
            tips.append(f'노출 대비 CTR {ctr*100:.1f}% 낮음 — 제목/메타 설명 후킹 개선')
        if pos > 20 and imp >= 500:
            tips.append('순위 20위 밖·노출 큼 — 본문 깊이(사례·수치·표) 보강 필요')
        if tips:
            out.append({'title': r.get('title'), 'url': r.get('url'),
                        'impressions': imp, 'clicks': clk, 'position': pos,
                        'ctr': round(ctr * 100, 1), 'suggestions': tips})
    out.sort(key=lambda x: x['impressions'], reverse=True)
    return out
