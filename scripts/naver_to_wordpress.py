# -*- coding: utf-8 -*-
"""네이버 블로그 글 → 구글(워드프레스) 이관 배치 도구.

이미 발행된 네이버 블로그 글(텍스트)을 받아서:
  1) 구글 SEO 형식으로 재작성 (브랜드 페르소나·선언문은 brand_profiles에서 자동 반영,
     "N년차" 같은 숫자 배지·카카오 오픈톡 CTA 제거)
  2) AI 씬 밝은 썸네일 + 본문 이미지 N장 새로 생성
  3) 워드프레스에 초안(기본)으로 발행 — 본문 최상단에 썸네일 삽입

입력 파일 형식 (UTF-8 텍스트):
    (1번 글 제목/본문 전체)
    =====
    (2번 글 …)
    =====
    ...
  글 사이는 '=====' 한 줄로 구분. 각 블록 첫 줄이 제목이어도 되고 아니어도 됨
  (재작성 단계가 알아서 SEO 제목을 새로 만든다). 네이버 편집기의
  'AI 활용 설정' / '사진 설명을 입력하세요.' 같은 잡음 줄은 자동 제거.

사용법 (로컬 또는 Render 쉘 — .env 또는 환경변수에 SUPABASE_URL/SERVICE_KEY/ENCRYPTION_KEY 필요):
    python scripts/naver_to_wordpress.py <입력파일.txt>
    python scripts/naver_to_wordpress.py <입력파일.txt> --limit 5          # 앞 5개만
    python scripts/naver_to_wordpress.py <입력파일.txt> --status publish   # 초안 대신 바로 발행
    python scripts/naver_to_wordpress.py <입력파일.txt> --body-images 2    # 본문 이미지 장수

키(fal_api_key·anthropic_api_key)는 saas_config DB에서 자동 로드되므로 별도 지정 불필요.
"""
import os
import re
import sys
import time
import base64
import argparse

# ── 프로젝트 루트를 import 경로에 추가 ─────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, '.env'))
except Exception:
    pass

# ── 대상 브랜드 (매실K) ──────────────────────────────────────
BRAND_ID = 'eada3911-dddf-4986-adfe-6a79dcde7407'
SCENE_STYLE = 'pastel_person'   # 밝은 파스텔 인물 일러스트
SCENE_THEME = 'baby_blue'       # 썸네일 색 테마
MODEL = 'claude-sonnet-4-6'
IMG_MAX_TRIES = 4               # 이미지 검증 실패 시 최대 재생성 횟수

import requests
from supabase import create_client


def _sb():
    url = os.environ.get('SUPABASE_URL', '')
    key = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY', '')
    if not url or not key:
        print('ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다.')
        sys.exit(1)
    return create_client(url, key)


def _clean_naver(text: str) -> str:
    """네이버 편집기 잡음 줄 제거 (이미지 자리표시자·구분선·제로폭 공백 등)."""
    NOISE = {'AI 활용 설정', 'AI 활용', '사진 설명을 입력하세요.', '사진 설명을 입력하세요', '---'}
    out = []
    for ln in text.splitlines():
        s = ln.replace('​', '').strip()   # 제로폭 공백(​) 제거
        if s in NOISE or set(s) <= {'ㅡ', '—', '-', '='}:   # 구분선류 스킵
            continue
        out.append(ln.replace('​', ''))
    return '\n'.join(out).strip()


def _brand_persona(sb) -> str:
    """brand_profiles.extra_context(브랜드 정체성·선언문) 반환."""
    try:
        r = sb.table('brand_profiles').select('name, extra_context, target_customer, brand_tone') \
            .eq('id', BRAND_ID).single().execute().data
        parts = [f"브랜드명: {r.get('name')}"]
        if r.get('target_customer'):
            parts.append(f"타겟 독자: {r['target_customer']}")
        if r.get('extra_context'):
            parts.append(r['extra_context'])
        return '\n'.join(parts)
    except Exception as e:
        print(f'  (경고) 브랜드 페르소나 조회 실패: {e}')
        return ''


# ── 1) 재작성 + 이미지 지시를 한 번의 Claude 호출로 ──────────────
_REWRITE_SYSTEM = """당신은 네이버 블로그 글을 구글(워드프레스) SEO 글로 옮기는 한국 커머스 에디터입니다.

아래 [브랜드 페르소나]의 정체성·선언문·말투를 글 전체에 반영합니다.

절대 규칙:
- 원문의 사실·경험·구체적 디테일은 그대로 살린다. 없는 사실을 지어내지 않는다.
- "N년차/12년차" 같은 연차 숫자 배지는 쓰지 않는다. 경력·경험의 신뢰감은 숫자 없이
  "오래 팔아오면서", "직접 겪어보니" 같은 표현으로 살린다.
- 카카오 오픈톡·"셀러 연구소"·외부 채팅방 유도 문구(CTA)는 전부 삭제한다.
- AI 티 나는 상투구("여러분", "강력 추천")를 피하고 원저자의 담백한 목소리를 유지한다.

반드시 아래 3개 블록을 순서대로, 이 구분자를 그대로 써서 출력한다:

[[[GOOGLE]]]
SEO 제목: (60자 이내, 핵심 검색어 앞배치)
메타 설명: (150자 이내)
슬러그: (영문 소문자-하이픈)
본문: (공백 포함 2,000자 내외. ## 소제목으로 구조화. 비교·정리 가능한 정보는
  마크다운 표 1개 이상. 원문보다 깊고 길게, 배경·팁 보강.
  [사진] 마커는 넣지 말 것 — 이미지는 별도 삽입됨)
FAQ: (### 질문 + 답변 3개)
태그: (쉼표로 구분한 키워드 8개)

[[[THUMB]]]
헤드라인: (썸네일용 15자 이내 핵심 문구)
서브: (썸네일용 20자 이내 보조 문구)
장면: (썸네일 일러스트로 그릴 밝고 긍정적인 장면 묘사, 한글 한 문장)
포커스 키워드: (구글 검색용 핵심 키워드 1개, 2~4단어, 제목의 핵심어와 일치)

[[[IMAGES]]]
(본문에 넣을 밝고 긍정적인 라이프스타일 이미지 생성 프롬프트를 영문으로 5줄,
 각 줄은 본문의 서로 다른 소제목 내용을 반영하고, 끝에 반드시 다음을 그대로 포함:
 ", photorealistic photograph, realistic real people, NOT illustration, NOT cartoon, NOT anime,
 absolutely no text, no letters, no chinese characters, bright warm daylight, positive uplifting mood".
 한 줄에 하나씩, 번호·기호 없이.)
"""


def rewrite(naver_text: str, persona: str) -> dict:
    from services.claude_service import generate_text
    user = f"[브랜드 페르소나]\n{persona}\n\n[옮길 네이버 원문]\n{naver_text[:6000]}"
    raw = generate_text(_REWRITE_SYSTEM, user, max_tokens=6000, model=MODEL)

    def _section(tag_start, tag_end):
        m = re.search(re.escape(tag_start) + r'(.*?)(?:' + re.escape(tag_end) + r'|$)', raw, re.S)
        return (m.group(1).strip() if m else '')

    google = _section('[[[GOOGLE]]]', '[[[THUMB]]]')
    thumb  = _section('[[[THUMB]]]', '[[[IMAGES]]]')
    images = _section('[[[IMAGES]]]', '\x00')

    headline = (re.search(r'헤드라인\s*[:：]\s*(.+)', thumb) or [None, ''])[1].strip()
    sub      = (re.search(r'서브\s*[:：]\s*(.+)', thumb) or [None, ''])[1].strip()
    scene    = (re.search(r'장면\s*[:：]\s*(.+)', thumb) or [None, ''])[1].strip()
    focus    = (re.search(r'포커스\s*키워드\s*[:：]\s*(.+)', thumb) or [None, ''])[1].strip()
    img_prompts = [l.strip(' -*·\t') for l in images.splitlines() if l.strip()][:5]

    return {'google': google, 'headline': headline, 'sub': sub,
            'scene': scene, 'focus': focus, 'img_prompts': img_prompts}


# ── 이미지 검증 (Claude Vision) ──────────────────────────────
def _validate_image(img_bytes: bytes, mime: str, expect_style: str) -> tuple:
    """생성 이미지 품질 검사 → (ok, reason).

    expect_style: 'photo'(실사) | 'illustration'(일러스트) | 'any'.
    걸러내는 것: ① 화면에 렌더된 CJK(중국어·일본어·한글)·엉터리 글자,
                ② 기대한 그림체와 다른 경우(실사인데 만화 등).
    검증 자체가 실패하면 통과 처리(무한루프 방지).
    """
    import json
    try:
        from services.claude_service import generate_with_images
        b64 = base64.b64encode(img_bytes).decode()
        # 실제 사용자 불만은 ① 명백한 만화/애니 드리프트 ② 화면 속 중국어 글자 두 가지뿐.
        #   부드러운/스타일리시한 실사도 반려되지 않도록 '명백한 경우만' 걸러낸다.
        q = ('Check this blog image. Reply ONLY compact JSON: '
             '{"cjk_text": true|false, "cartoon": true|false, "bad_anatomy": true|false}. '
             'cjk_text=true ONLY if real Chinese or Japanese characters, or clearly garbled fake '
             'text, are prominently rendered in the image. Normal English words or incidental '
             'blur = false. '
             'cartoon=true ONLY if the image is clearly a drawn cartoon / anime / vector '
             'illustration. A real photograph — even softly lit, warm, or stylish — = false. '
             'bad_anatomy=true if a person has an OBVIOUS anatomical error: an extra hand or arm, '
             'more than two hands, fused / extra / missing fingers, or a clearly deformed hand or '
             'limb. Correct normal hands and body = false.')
        out = generate_with_images('You are an image QA checker. Reply only JSON.',
                                   q, images=[(b64, mime)], max_tokens=120)
        m = re.search(r'\{.*\}', out, re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception as e:
        return True, f'검증skip({str(e)[:40]})'
    if d.get('cjk_text'):
        return False, '중국어/엉터리 글자'
    if expect_style == 'photo' and d.get('cartoon'):
        return False, '만화체'
    if expect_style == 'photo' and d.get('bad_anatomy'):
        return False, '손/신체 이상'
    return True, 'ok'


def _fetch(url: str) -> tuple:
    r = requests.get(url, timeout=40); r.raise_for_status()
    return r.content, (r.headers.get('Content-Type') or 'image/jpeg').split(';')[0].strip()


# ── 2) AI 씬 썸네일 (검증+재생성) ────────────────────────────
def make_thumbnail(sb, user_id, headline, sub, scene_topic) -> str:
    from services.imagen_service import generate_scene, upload_to_supabase
    from services.thumbnail_studio import render_thumbnail
    scene_bytes = None
    for t in range(IMG_MAX_TRIES):
        scene_url = generate_scene([], topic=scene_topic or headline, user_id=user_id,
                                   bg_color='soft pastel baby blue', style=SCENE_STYLE, supabase=sb)
        content, mime = _fetch(scene_url)
        ok, reason = _validate_image(content, mime, 'illustration')
        if ok:
            scene_bytes = content
            break
        print(f'      썸네일 씬 재생성({t+1}/{IMG_MAX_TRIES}): {reason}')
        scene_bytes = content   # 마지막 것 보관
    png = render_thumbnail(headline=headline[:15], sub=sub[:20], badge='', cta='',
                           theme=SCENE_THEME, bg_image=scene_bytes, title_style='banner')
    b64 = 'data:image/png;base64,' + base64.b64encode(png).decode()
    return upload_to_supabase(b64, user_id, f'thumb_scene_{int(time.time()*1000)}.png', supabase=sb)


# ── 3) 본문 이미지 (검증+재생성, 실사 강제) ──────────────────
# 사진 프롬프트를 앞에 강하게 배치해 FLUX의 만화 드리프트를 억제.
_PHOTO_PREFIX = ('Professional editorial photograph, photorealistic, shot on DSLR, '
                 'natural realistic lighting, real people. ')
_PHOTO_RETRY  = ('RAW candid documentary photograph, ultra realistic real human photo, '
                 'absolutely NOT illustration, NOT cartoon, NOT anime, NOT drawing. ')


def make_body_images(prompts, n) -> list:
    from services.imagen_service import generate_image
    urls = []
    for p in prompts[:n]:
        good = None
        for t in range(IMG_MAX_TRIES):
            full = (_PHOTO_RETRY if t > 0 else _PHOTO_PREFIX) + p
            try:
                url, _ = generate_image(full, engine='flux_dev', size='1024x1024')
            except Exception as e:
                print(f'  (경고) 본문 이미지 생성 실패: {e}')
                break
            try:
                content, mime = _fetch(url)
                ok, reason = _validate_image(content, mime, 'photo')
            except Exception:
                ok, reason = True, ''   # 다운로드/검증 실패 시 통과
            if ok:
                good = url
                break
            print(f'      본문 이미지 재생성({t+1}/{IMG_MAX_TRIES}): {reason}')
        if good:
            urls.append(good)
        else:
            print('      → 실사 확보 실패, 이 이미지는 건너뜀(만화 삽입 방지)')
    return urls


# ── 4) 발행 ──────────────────────────────────────────────────
def publish(sb, google_text, body_urls, thumb_url, status, focus_kw=''):
    from services.wordpress_publish import create_full_post
    from services.wordpress_connection import get_client_for_user
    res = create_full_post(sb, BRAND_ID, google_text,
                           body_image_urls=body_urls, thumbnail_url=thumb_url, status=status)
    if not res.get('ok'):
        return res
    try:
        client = get_client_for_user(BRAND_ID, supabase=sb)
        pid = res['post_id']
        post = client._request('GET', f'/posts/{pid}', params={'context': 'edit'})
        fid = post.get('featured_media')
        html = (post.get('content') or {}).get('raw') or ''
        # 본문 최상단에도 썸네일 삽입 (대표이미지 패널 외에 본문에서도 보이게)
        if fid:
            media = client._request('GET', f'/media/{fid}')
            src = media.get('source_url') or ''
            if src and src not in html:
                fig = f'<figure class="wp-block-image size-large"><img src="{src}" alt=""/></figure>\n'
                client._request('POST', f'/posts/{pid}', json_body={'content': fig + html})
        # Rank Math 포커스 키워드 설정 (스니펫으로 meta 등록돼 있어야 반영됨)
        if focus_kw:
            try:
                client._request('POST', f'/posts/{pid}',
                                json_body={'meta': {'rank_math_focus_keyword': focus_kw}})
            except Exception as e:
                print(f'  (경고) 포커스 키워드 설정 실패(무시): {e}')
    except Exception as e:
        print(f'  (경고) 후처리 실패(무시): {e}')
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input_file', help='=====로 구분된 네이버 글 텍스트 파일')
    ap.add_argument('--status', default='draft', choices=['draft', 'publish', 'pending'])
    ap.add_argument('--limit', type=int, default=0, help='앞 N개만 (0=전체)')
    ap.add_argument('--body-images', type=int, default=5, help='본문 이미지 장수 (기본 5)')
    ap.add_argument('--start', type=int, default=1, help='몇 번째 글부터 (1-base, 재시도용)')
    args = ap.parse_args()

    sb = _sb()
    from services.config_service import get_config
    os.environ.setdefault('ANTHROPIC_API_KEY', get_config('anthropic_api_key', _supabase=sb) or '')
    if not get_config('fal_api_key', _supabase=sb):
        print('ERROR: fal_api_key 를 찾을 수 없습니다 (이미지 생성 불가).')
        sys.exit(1)

    user_id = None
    try:
        b = sb.table('brand_profiles').select('user_id').eq('id', BRAND_ID).single().execute().data
        user_id = b.get('user_id')
    except Exception:
        pass

    persona = _brand_persona(sb)
    raw = open(args.input_file, encoding='utf-8').read()
    blocks = [_clean_naver(b) for b in re.split(r'(?m)^\s*={3,}\s*$', raw)]
    blocks = [b for b in blocks if b.strip()]
    if args.limit:
        blocks = blocks[:args.limit]
    print(f'총 {len(blocks)}개 글 처리 (status={args.status}, 본문이미지={args.body_images}장)\n')

    results = []
    for i, block in enumerate(blocks, 1):
        if i < args.start:
            continue
        head = block.splitlines()[0][:40] if block.splitlines() else ''
        print(f'[{i}/{len(blocks)}] {head} …')
        try:
            r = rewrite(block, persona)
            if not r['google']:
                raise RuntimeError('재작성 결과가 비어 있음')
            thumb = make_thumbnail(sb, user_id, r['headline'] or head, r['sub'], r['scene'])
            body_urls = make_body_images(r['img_prompts'], args.body_images)
            res = publish(sb, r['google'], body_urls, thumb, args.status, focus_kw=r.get('focus', ''))
            if res.get('ok'):
                print(f"    OK post_id={res['post_id']}  {res.get('edit_link')}")
                results.append((i, res['post_id'], res.get('edit_link')))
            else:
                print(f"    실패: {res.get('message')}")
                results.append((i, None, res.get('message')))
        except Exception as e:
            print(f'    오류: {e}')
            results.append((i, None, str(e)[:150]))

    print('\n===== 결과 요약 =====')
    for i, pid, info in results:
        print(f'  {i}: ' + (f'post {pid} — {info}' if pid else f'실패 — {info}'))
    ok = sum(1 for _, pid, _ in results if pid)
    print(f'\n완료: {ok}/{len(results)} 성공')


if __name__ == '__main__':
    main()
