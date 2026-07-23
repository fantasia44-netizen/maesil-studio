# -*- coding: utf-8 -*-
"""워드프레스 글의 특정 본문 이미지를 새 이미지로 교체.

AI 이미지가 가끔 이상하게(손 기형·엉뚱한 구도) 나올 때, 그 이미지 한 장만
새로 생성(검증 통과)해서 교체한다. 글·다른 이미지·썸네일은 그대로 둔다.

사용법:
    python scripts/replace_body_image.py <post_id> <img_index>
    python scripts/replace_body_image.py <post_id> <img_index> "영문 프롬프트"

  · img_index: 글 안 <img> 순서(0부터). 0은 보통 대표 썸네일이므로 본문 이미지는 1,2,3…
  · 프롬프트 생략 시 글 제목 기반의 밝은 라이프스타일 이미지로 생성.
  · 먼저 현재 이미지 목록을 보려면 img_index 자리에 list 입력:
        python scripts/replace_body_image.py <post_id> list
"""
import os
import re
import sys
import base64

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, '.env'))
except Exception:
    pass

import requests
from supabase import create_client

BRAND_ID = 'eada3911-dddf-4986-adfe-6a79dcde7407'

# 메인 배치 스크립트의 검증·생성 로직 재사용
import importlib.util
_spec = importlib.util.spec_from_file_location(
    'n2w', os.path.join(_ROOT, 'scripts', 'naver_to_wordpress.py'))
n2w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(n2w)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    post_id = sys.argv[1]
    sub = sys.argv[2]

    sb = create_client(os.environ['SUPABASE_URL'],
                       os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY', ''))
    from services.config_service import get_config
    os.environ.setdefault('ANTHROPIC_API_KEY', get_config('anthropic_api_key', _supabase=sb) or '')
    from services.wordpress_connection import get_client_for_user
    client = get_client_for_user(BRAND_ID, supabase=sb)

    post = client._request('GET', f'/posts/{post_id}', params={'context': 'edit'})
    html = (post.get('content') or {}).get('raw') or ''
    title = (post.get('title') or {}).get('raw') or ''
    imgs = re.findall(r'<img[^>]+src="([^"]+)"', html)

    if sub == 'list':
        print(f'글: {title}')
        for i, u in enumerate(imgs):
            print(f'  [{i}] {u}')
        print('\n교체하려면: python scripts/replace_body_image.py '
              f'{post_id} <index> ["영문 프롬프트"]')
        return

    idx = int(sub)
    if idx < 0 or idx >= len(imgs):
        print(f'이미지 인덱스 범위 오류 (0~{len(imgs)-1})')
        sys.exit(1)
    old_src = imgs[idx]

    prompt = sys.argv[3] if len(sys.argv) >= 4 else (
        f'a bright positive Korean lifestyle scene related to: {title}. '
        'a real Korean person in a warm sunny space, natural candid moment')

    print(f'글: {title}')
    print(f'교체 대상 [{idx}]: {old_src}')
    print('새 이미지 생성 중(검증 포함)...')
    new_urls = n2w.make_body_images([prompt], 1)
    if not new_urls:
        print('실사 이미지 확보 실패 — 교체 취소. 프롬프트를 바꿔 다시 시도하세요.')
        sys.exit(1)

    # 새 이미지를 WP 미디어로 업로드 → source_url
    from services.wordpress_publish import _upload_image_to_wp
    up = _upload_image_to_wp(client, new_urls[0], f'replace_{post_id}_{idx}')
    new_src = (up or {}).get('source_url')
    if not new_src:
        print('WP 업로드 실패 — 교체 취소.')
        sys.exit(1)

    # HTML에서 해당 src만 교체
    new_html = html.replace(old_src, new_src, 1)
    client._request('POST', f'/posts/{post_id}', json_body={'content': new_html})
    print(f'교체 완료 → {new_src}')
    print(f'편집: {client.site}/wp-admin/post.php?post={post_id}&action=edit')


if __name__ == '__main__':
    main()
