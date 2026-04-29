"""광고·표시 규정 안내 + 카테고리별 시스템 금지어 + 결과 검증.

3-Tier 금지어 시스템:
  1) 시스템 레벨  — 카테고리별 법적 규제 (saas_config 시드)
  2) 브랜드 레벨  — brand_profiles.avoid_words (사용자 입력)
  3) 상품 레벨   — products.avoid_words (사용자 입력)

세 레이어를 합집합으로 프롬프트에 주입 + 결과 검증 시 사용.
"""
import json
import logging
from typing import Iterable

from flask import current_app

logger = logging.getLogger(__name__)


# 카테고리 → saas_config 키 매핑
_CATEGORY_TO_KEYWORD_KEY = {
    'general':           'regulatory_keywords_general',
    'food':              'regulatory_keywords_food',
    'baby_food':         'regulatory_keywords_baby_food',
    'health_supplement': 'regulatory_keywords_health_supplement',
    'cosmetics':         'regulatory_keywords_cosmetics',
    'medical_device':    'regulatory_keywords_medical_device',
}


def _get_saas_config(key: str, default: str = '') -> str:
    """saas_config 에서 텍스트 값 조회 — 미존재 시 default."""
    sb = current_app.supabase
    if not sb:
        return default
    try:
        r = (sb.table('saas_config')
             .select('value_text')
             .eq('key', key)
             .limit(1)
             .execute())
        if r and r.data:
            return r.data[0].get('value_text') or default
    except Exception as e:
        logger.debug(f'[regulatory] saas_config({key}) 조회 실패: {e}')
    return default


def _parse_json_list(text: str) -> list[str]:
    if not text:
        return []
    try:
        v = json.loads(text)
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
    except (ValueError, TypeError):
        pass
    return []


# ─────────────────────────────────────────────────────────────
# 1. 시스템 레벨 — 카테고리별 법적 금지어
# ─────────────────────────────────────────────────────────────

def get_system_avoid_words(category: str) -> list[str]:
    """카테고리별 법적 금지/주의 표현 + 일반 금지어 합집합."""
    if not category:
        category = 'general'
    keys = ['regulatory_keywords_general']
    cat_key = _CATEGORY_TO_KEYWORD_KEY.get(category)
    if cat_key and cat_key not in keys:
        keys.append(cat_key)
    out: list[str] = []
    seen = set()
    for k in keys:
        for w in _parse_json_list(_get_saas_config(k, '')):
            if w not in seen:
                seen.add(w)
                out.append(w)
    return out


# ─────────────────────────────────────────────────────────────
# 2~3. 3-Tier 합집합
# ─────────────────────────────────────────────────────────────

def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()] if str(value).strip() else []


def combine_avoid_words(brand: dict | None,
                        product: dict | None,
                        category: str) -> list[str]:
    """3-tier 금지어 합집합 (시스템 ∪ 브랜드 ∪ 상품)."""
    out: list[str] = []
    seen: set[str] = set()
    layers: Iterable[list[str]] = (
        get_system_avoid_words(category),
        _as_list((brand or {}).get('avoid_words')),
        _as_list((product or {}).get('avoid_words')),
    )
    for layer in layers:
        for w in layer:
            if w and w not in seen:
                seen.add(w)
                out.append(w)
    return out


# ─────────────────────────────────────────────────────────────
# 디스클레이머
# ─────────────────────────────────────────────────────────────

def _disclaimer_kind_for(category: str) -> str:
    """카테고리 → disclaimer 키 ('general' or 'regulated')."""
    if not category:
        return 'general'
    raw = _get_saas_config('disclaimer_category_map', '')
    if raw:
        try:
            m = json.loads(raw)
            if isinstance(m, dict):
                return m.get(category, 'general')
        except (ValueError, TypeError):
            pass
    # 폴백: 식품/이유식/건기식/화장품/의료기기는 강한 디스클레이머
    if category in {'food', 'baby_food', 'health_supplement',
                    'cosmetics', 'medical_device'}:
        return 'regulated'
    return 'general'


def get_disclaimer(category: str) -> str:
    """카테고리에 맞는 디스클레이머 본문."""
    kind = _disclaimer_kind_for(category)
    key = f'disclaimer_{kind}'
    return _get_saas_config(key, '').strip()


def append_disclaimer(text: str, category: str) -> str:
    """생성 결과 하단에 디스클레이머 부착 (이미 들어있으면 중복 방지)."""
    if not text:
        return text
    disclaimer = get_disclaimer(category)
    if not disclaimer:
        return text
    # 단순 중복 검사 — 이미 같은 문구가 들어있으면 스킵
    head = disclaimer.splitlines()[0].strip() if disclaimer else ''
    if head and head in text:
        return text
    return f'{text.rstrip()}\n\n---\n\n{disclaimer}'


# ─────────────────────────────────────────────────────────────
# 결과 검증
# ─────────────────────────────────────────────────────────────

def scan_violations(text: str, avoid_words: list[str]) -> list[str]:
    """본문에서 검출된 금지어 목록 (대소문자 무시 부분일치).

    한국어 특성상 어절 경계가 모호 → substring 매칭. 너무 짧은 단어
    (1자)는 오탐이 많아 제외.
    """
    if not text or not avoid_words:
        return []
    found: list[str] = []
    seen = set()
    haystack = text.lower()
    for w in avoid_words:
        w = (w or '').strip()
        if len(w) < 2:
            continue
        if w.lower() in haystack and w not in seen:
            seen.add(w)
            found.append(w)
    return found
