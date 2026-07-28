"""experience_store — 경험 데이터 저장소.

운영자의 실제 사업 경험(문제/조치/결과/수치)을 저장·검색하고, 블로그 생성 시
주제와 매칭되는 경험을 프롬프트 근거로 포맷한다. AI가 없는 수치를 지어내지
못하게 하는 E-E-A-T 데이터 소스.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_STOPWORDS = {
    '방법', '이유', '전략', '가이드', '정리', '분석', '하는', '위한', '어떻게',
    '그리고', '하지만', '가장', '실전', '완전', '총정리', '추천',
}


def _tokens(text: str) -> list[str]:
    """한글/영문/숫자 토큰 추출(2자 이상, 불용어 제외)."""
    if not text:
        return []
    raw = re.findall(r'[가-힣A-Za-z0-9]{2,}', text)
    return [t for t in raw if t not in _STOPWORDS]


def list_records(supabase, *, brand_id: str | None = None,
                 usable_only: bool = False, limit: int = 200) -> list[dict]:
    try:
        q = supabase.table('experience_records').select('*')
        if brand_id:
            # 브랜드 전용 + 공통(brand_id null) 모두
            q = q.or_(f'brand_id.eq.{brand_id},brand_id.is.null')
        if usable_only:
            q = q.eq('usable_for_content', True)
        r = q.order('created_at', desc=True).limit(limit).execute()
        return r.data or []
    except Exception as e:
        logger.warning('[experience_store] list 실패: %s', e)
        return []


def create_record(supabase, row: dict) -> dict | None:
    try:
        r = supabase.table('experience_records').insert(row).execute()
        return (r.data or [None])[0]
    except Exception as e:
        logger.error('[experience_store] create 실패: %s', e)
        return None


def delete_record(supabase, record_id: str, user_id: str | None = None) -> bool:
    try:
        q = supabase.table('experience_records').delete().eq('id', record_id)
        if user_id:
            q = q.eq('user_id', user_id)
        q.execute()
        return True
    except Exception as e:
        logger.error('[experience_store] delete 실패: %s', e)
        return False


def _score(record: dict, query_tokens: set[str]) -> int:
    """경험 record와 주제 토큰의 매칭 점수."""
    hay = set()
    hay.update(_tokens(record.get('title', '')))
    hay.update(_tokens(record.get('summary', '')))
    hay.update(_tokens(record.get('category', '')))
    hay.update(_tokens(record.get('platform', '')))
    hay.update(_tokens(record.get('problem', '')))
    for kw in (record.get('keywords') or []):
        hay.update(_tokens(kw))
    return len(query_tokens & hay)


def search_relevant(supabase, *, brand_id: str | None,
                    topic: str, keyword: str = '', seo_keywords: str = '',
                    top_k: int = 4, min_score: int = 1) -> list[dict]:
    """주제/키워드에 매칭되는 사용 가능 경험을 점수순으로 반환."""
    records = list_records(supabase, brand_id=brand_id, usable_only=True, limit=300)
    if not records:
        return []
    qtokens = set(_tokens(f'{topic} {keyword} {seo_keywords}'))
    if not qtokens:
        return []
    scored = [(rec, _score(rec, qtokens)) for rec in records]
    scored = [(rec, s) for rec, s in scored if s >= min_score]
    scored.sort(key=lambda t: t[1], reverse=True)
    return [rec for rec, _ in scored[:top_k]]


def _fmt_numbers(numbers: dict | None) -> str:
    if not numbers:
        return ''
    parts = []
    for k, v in numbers.items():
        parts.append(f'{k}={v}')
    return ', '.join(parts)


_FIELD_CAP = 280   # 항목별 최대 글자(토큰 방어)
_BLOCK_CAP = 1800  # 경험 블록 전체 최대 글자


def _trim(s: str, cap: int = _FIELD_CAP) -> str:
    s = (s or '').strip()
    return s if len(s) <= cap else s[:cap] + '…'


def format_for_prompt(records: list[dict]) -> str:
    """검색된 경험을 프롬프트 주입용 텍스트로 포맷.

    confidentiality=private 는 제외(공개 불가), anonymized 는 회사/고객명 노출 주의 표시.
    항목·전체 길이를 캡핑해 입력 토큰이 과도하게 커지지 않게 한다.
    """
    usable = [r for r in records if (r.get('confidentiality') or 'anonymized') != 'private']
    if not usable:
        return ''
    lines = []
    for i, r in enumerate(usable, 1):
        block = [f'{i}. {_trim(r.get("title", ""), 120)}']
        if r.get('problem'):
            block.append(f'   - 문제: {_trim(r["problem"])}')
        if r.get('action'):
            block.append(f'   - 조치: {_trim(r["action"])}')
        if r.get('result'):
            block.append(f'   - 결과: {_trim(r["result"])}')
        nums = _fmt_numbers(r.get('numbers_json'))
        if nums:
            block.append(f'   - 수치(사용 가능): {_trim(nums, 200)}')
        if (r.get('confidentiality') or 'anonymized') == 'anonymized':
            block.append('   - ※ 회사명/고객사명은 익명 처리할 것')
        lines.append('\n'.join(block))
    out = '\n'.join(lines)
    return out if len(out) <= _BLOCK_CAP else out[:_BLOCK_CAP] + '\n…(이하 생략)'


# ── 빠른 입력: 자유 텍스트 → 구조화 (Claude Haiku) ──────────────────────

_STRUCTURE_PROMPT = '''다음은 온라인 커머스 운영자가 자유롭게 적은 실제 경험 메모다.
이를 아래 JSON 스키마로 구조화하라. 없는 내용을 지어내지 말고, 메모에 있는
사실만 사용하라. 숫자는 메모에 나온 것만 numbers에 넣어라.

메모:
"""{memo}"""

JSON만 출력(설명 금지):
{{
  "category": "광고|네이버|쿠팡|제조|물류|3PL|브랜드|ERP|AI|자금|기타 중 하나",
  "title": "한 줄 제목",
  "summary": "1~2문장 요약",
  "problem": "당시 문제",
  "action": "실제 취한 조치",
  "result": "결과",
  "numbers": {{"항목명": 숫자}},
  "platform": "쿠팡|네이버|자사몰 등 (없으면 빈 문자열)",
  "keywords": ["매칭용 키워드 3~6개"]
}}'''


def structure_free_text(memo: str, anthropic_key: str) -> dict:
    """자유 메모 → 구조화 dict. 실패 시 {} (호출부에서 수동 입력 폴백)."""
    if not memo.strip() or not anthropic_key:
        return {}
    try:
        import json
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=800,
            messages=[{'role': 'user',
                       'content': _STRUCTURE_PROMPT.format(memo=memo[:2000])}])
        text = msg.content[0].text.strip()
        if '```' in text:
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        logger.warning('[experience_store] 구조화 실패: %s', e)
        return {}
