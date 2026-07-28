"""internal_links — 발행 콘텐츠 인덱스 + 내부 링크 자동 추천.

워드프레스 발행 성공 시 글을 인덱싱하고, 새 글 생성 시 관련 발행글을 검색해
본문에 자연스러운 내부 링크로 제안한다(체류시간·SEO).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_STOPWORDS = {'방법', '이유', '전략', '가이드', '정리', '분석', '하는', '위한', '추천', '완전', '총정리'}


def _tokens(text: str) -> list[str]:
    if not text:
        return []
    return [t for t in re.findall(r'[가-힣A-Za-z0-9]{2,}', text) if t not in _STOPWORDS]


def index_post(supabase, *, brand_id: str | None, title: str, url: str,
               slug: str = '', summary: str = '', tags: list[str] | None = None,
               category: str | None = None, wp_post_id=None) -> None:
    """발행글을 인덱스에 upsert(url 유니크). 실패해도 발행 흐름을 막지 않음."""
    if not url or not title:
        return
    keywords = list(tags or [])
    keywords += _tokens(title)
    row = {
        'brand_id': brand_id, 'wp_post_id': wp_post_id,
        'title': title[:300], 'url': url, 'slug': slug or None,
        'summary': (summary or '')[:500] or None,
        'keywords': list(dict.fromkeys(keywords))[:20],
        'category': category or None,
    }
    try:
        supabase.table('published_content_index').upsert(
            row, on_conflict='url').execute()
    except Exception as e:
        logger.warning('[internal_links] 인덱싱 실패(무시): %s', e)


def search_related(supabase, *, brand_id: str | None, topic: str,
                   keyword: str = '', seo_keywords: str = '',
                   top_k: int = 4, exclude_url: str | None = None) -> list[dict]:
    try:
        q = supabase.table('published_content_index').select(
            'title, url, keywords, summary')
        if brand_id:
            q = q.or_(f'brand_id.eq.{brand_id},brand_id.is.null')
        rows = q.limit(500).execute().data or []
    except Exception as e:
        logger.warning('[internal_links] 조회 실패: %s', e)
        return []
    qtokens = set(_tokens(f'{topic} {keyword} {seo_keywords}'))
    if not qtokens:
        return []
    scored = []
    for r in rows:
        if exclude_url and r.get('url') == exclude_url:
            continue
        hay = set(_tokens(r.get('title', ''))) | set(
            t for kw in (r.get('keywords') or []) for t in _tokens(kw))
        s = len(qtokens & hay)
        if s >= 1:
            scored.append((r, s))
    scored.sort(key=lambda t: t[1], reverse=True)
    return [r for r, _ in scored[:top_k]]


def format_for_prompt(posts: list[dict]) -> str:
    if not posts:
        return ''
    lines = [f'- {p.get("title", "").strip()} → {p.get("url", "").strip()}' for p in posts]
    return '\n'.join(lines)
