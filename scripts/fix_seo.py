# -*- coding: utf-8 -*-
"""매실K 워드프레스 글들의 Rank Math 온페이지 점수 보정.

각 글에 대해:
  1) 포커스 키워드를 '제목에 그대로 들어있는' 핵심 문구로 교정
     (Rank Math는 키워드를 연속 문자열로 제목/본문/소제목에서 찾으므로,
      제목에 없는 조합이면 점수가 크게 깎인다)
  2) 본문 이미지 alt 텍스트에 키워드를 삽입 (비어있던 alt="" 채움)
  → 구글 이미지 검색·접근성·Rank Math 점수 동시 개선. 이미지 재생성 없음.

사용: python scripts/fix_seo.py            # 전체(초안+발행) 처리
      python scripts/fix_seo.py 197 205    # 특정 post_id만
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, '.env'))
except Exception:
    pass

from supabase import create_client

BRAND_ID = 'eada3911-dddf-4986-adfe-6a79dcde7407'


def pick_keyword(title: str) -> str:
    """제목에 그대로 들어있는 2~4단어 포커스 키워드."""
    from services.claude_service import generate_text
    sysmsg = ('너는 한국 블로그 SEO 담당자다. 주어진 제목에서 구글 검색 포커스 키워드로 쓸 '
              '2~4단어 핵심 문구를 고른다. 반드시 제목 안에 공백 포함 "그대로 연속으로" '
              '등장하는 문구여야 한다(부제 — 뒤쪽도 가능). 키워드 문구만 출력, 따옴표·설명 금지.')
    try:
        kw = generate_text(sysmsg, f'제목: {title}', max_tokens=30).strip().strip('"\'' ).strip()
    except Exception:
        kw = ''
    # 검증: 제목에 실제로 들어있는지 (공백 정규화 후)
    norm = re.sub(r'\s+', ' ', title)
    if kw and re.sub(r'\s+', ' ', kw) in norm:
        return kw
    # 폴백: 제목 앞부분(부제 구분자 앞) 3~4단어
    head = re.split(r'\s[—–-]\s', title)[0]
    words = head.split()
    return ' '.join(words[:4]) if words else title[:20]


def main():
    only = set(sys.argv[1:])
    sb = create_client(os.environ['SUPABASE_URL'],
                       os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY', ''))
    from services.config_service import get_config
    os.environ.setdefault('ANTHROPIC_API_KEY', get_config('anthropic_api_key', _supabase=sb) or '')
    from services.wordpress_connection import get_client_for_user
    client = get_client_for_user(BRAND_ID, supabase=sb)

    posts = []
    for st in ('publish', 'draft', 'pending'):
        try:
            posts += client._request('GET', '/posts',
                                     params={'status': st, 'per_page': 50, 'context': 'edit'}) or []
        except Exception:
            pass

    for p in posts:
        pid = str(p.get('id'))
        if only and pid not in only:
            continue
        title = (p.get('title') or {}).get('raw') or ''
        html = (p.get('content') or {}).get('raw') or ''
        kw = pick_keyword(title)

        # 이미지 alt 채우기 (비어있는 alt="" → 키워드)
        new_html, n_alt = re.subn(r'alt=""', f'alt="{kw}"', html)

        body = {'meta': {'rank_math_focus_keyword': kw}}
        if new_html != html:
            body['content'] = new_html
        try:
            client._request('POST', f'/posts/{pid}', json_body=body)
            print(f'post {pid}: 키워드="{kw}"  alt채움={n_alt}  | {title[:34]}')
        except Exception as e:
            print(f'post {pid}: 실패 {e}')


if __name__ == '__main__':
    main()
