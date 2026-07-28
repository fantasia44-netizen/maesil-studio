"""product_reference — 연결된 실제 상품(매실인사이트 임포트)을 블로그 근거로 자동 주입.

매실인사이트 상품가져오기로 로컬 products 테이블에 저장된 실제 상품(이름·가격·
카테고리·특징)을 주제와 매칭해 프롬프트에 사실 근거로 넣는다. 수동 입력 없이
연결만 돼 있으면 자동. (광고 성과 등은 외부 API 미노출 → 이 소스엔 없음)
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_STOPWORDS = {'방법', '이유', '전략', '가이드', '정리', '분석', '추천', '완전', '총정리'}


def _tokens(text: str) -> list[str]:
    if not text:
        return []
    return [t for t in re.findall(r'[가-힣A-Za-z0-9]{2,}', text) if t not in _STOPWORDS]


def _fetch_brand_products(supabase, brand_id: str, limit: int = 100) -> list[dict]:
    try:
        r = (supabase.table('products')
             .select('name, category, price, description, features, product_url')
             .eq('brand_id', brand_id).eq('is_active', True)
             .eq('source', 'maesil_insight')
             .order('created_at', desc=True).limit(limit).execute())
        return r.data or []
    except Exception as e:
        logger.warning('[product_reference] 조회 실패: %s', e)
        return []


def search_relevant(supabase, *, brand_id: str | None, topic: str,
                    keyword: str = '', seo_keywords: str = '', top_k: int = 5) -> list[dict]:
    """주제/키워드에 매칭되는 실제 상품을 점수순 반환. 매칭 0이면 최신 몇 개 폴백."""
    if not brand_id:
        return []
    products = _fetch_brand_products(supabase, brand_id)
    if not products:
        return []
    qtokens = set(_tokens(f'{topic} {keyword} {seo_keywords}'))
    if not qtokens:
        return products[:top_k]
    scored = []
    for p in products:
        hay = set(_tokens(p.get('name', ''))) | set(_tokens(p.get('category', '')))
        feats = p.get('features') or []
        for f in (feats if isinstance(feats, list) else [feats]):
            hay |= set(_tokens(str(f)))
        scored.append((p, len(qtokens & hay)))
    scored.sort(key=lambda t: t[1], reverse=True)
    top = [p for p, s in scored if s >= 1][:top_k]
    return top or products[:min(3, top_k)]   # 매칭 없으면 대표 상품 소수 폴백


def format_for_prompt(products: list[dict]) -> str:
    if not products:
        return ''
    lines = []
    for p in products[:5]:
        bits = [p.get('name', '').strip()]
        if p.get('category'):
            bits.append(f'[{p["category"]}]')
        if p.get('price'):
            bits.append(f'{int(p["price"]):,}원')
        line = ' '.join(b for b in bits if b)
        feats = p.get('features') or []
        feats = feats if isinstance(feats, list) else [feats]
        if feats:
            line += ' — ' + ', '.join(str(f) for f in feats[:3])[:120]
        lines.append(f'- {line}')
    return '\n'.join(lines)
