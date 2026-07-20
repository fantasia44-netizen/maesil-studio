"""구글(워드프레스)판 텍스트 → 실제 워드프레스 글 발행 — 공용 로직.

Flask 요청(블루프린트)과 Celery 워커(자동 발행) 양쪽에서 재사용하기 위해
current_app 에 의존하지 않고 supabase 클라이언트를 명시적으로 받는다.
"""
import logging
import re

import requests

from services.wordpress_client import WordPressError, friendly_error_message
from services.wordpress_connection import get_client_for_user, mark_used, mark_error

logger = logging.getLogger(__name__)

# ── 구글판 텍스트 → 워드프레스 글 필드 파싱 ──────────────────

_WP_LABELS = [
    (re.compile(r'^\s*[#*\s]*SEO\s*제목\s*[:：]\s*(.*)$', re.IGNORECASE),  'title'),
    (re.compile(r'^\s*[#*\s]*메타\s*설명\s*[:：]\s*(.*)$', re.IGNORECASE),  'excerpt'),
    (re.compile(r'^\s*[#*\s]*슬러그\s*[:：]\s*(.*)$', re.IGNORECASE),        'slug'),
    (re.compile(r'^\s*[#*\s]*본문\s*[:：]\s*(.*)$', re.IGNORECASE),          'body'),
    (re.compile(r'^\s*[#*\s]*FAQ\s*[:：]\s*(.*)$', re.IGNORECASE),           'faq'),
    (re.compile(r'^\s*[#*\s]*태그\s*[:：]\s*(.*)$', re.IGNORECASE),          'tags'),
]


def _slugify(s: str) -> str:
    """영문/숫자/하이픈만 남긴 슬러그 (없으면 '' → WP 자동 생성)."""
    s = (s or '').strip().lower()
    s = re.sub(r'[^a-z0-9\-]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:80]


def parse_google_post(text: str) -> dict:
    """구글(워드프레스)판 원문 → {title, excerpt, slug, tags[], html}.

    형식(experience.py/blog.py 프롬프트 참고):
        SEO 제목: ...
        메타 설명: ...
        슬러그: ...
        본문: ## 소제목 / 표 / [사진 N] / 알트텍스트: ...
        FAQ: ### 질문 + 답변
        태그: a, b, c
    형식을 못 지킨 응답도 폴백 처리(전체를 본문으로).
    """
    sections: dict = {}
    current = None
    buf: list = []

    def _flush():
        if current is not None:
            sections[current] = ('\n'.join(buf)).strip()

    for line in (text or '').splitlines():
        matched = False
        for pat, key in _WP_LABELS:
            m = pat.match(line)
            if m:
                _flush()
                current = key
                buf = [m.group(1)] if m.group(1).strip() else []
                matched = True
                break
        if not matched:
            if current is None:
                current = 'body'   # 라벨 전 서두는 본문으로
            buf.append(line)
    _flush()

    title   = (sections.get('title') or '').strip()
    excerpt = (sections.get('excerpt') or '').strip()
    slug    = _slugify(sections.get('slug') or '')
    body_md = (sections.get('body') or '').strip()
    faq_md  = (sections.get('faq') or '').strip()

    # 제목/본문 폴백
    if not body_md:
        body_md = (text or '').strip()
    if not title:
        for ln in body_md.splitlines():
            if ln.strip():
                title = ln.strip().lstrip('#').strip()[:120]
                break

    # 태그 파싱 (쉼표/가운뎃점/줄바꿈 구분)
    tags_raw = sections.get('tags') or ''
    tags = [t.strip().lstrip('#') for t in re.split(r'[,\n·、]', tags_raw) if t.strip()]

    # FAQ 를 본문 뒤에 붙임 (소제목이 없으면 헤더 추가)
    combined_md = body_md
    if faq_md:
        if not re.search(r'(?im)^#{1,3}\s*(faq|자주\s*묻는)', faq_md):
            combined_md += '\n\n## 자주 묻는 질문(FAQ)\n\n' + faq_md
        else:
            combined_md += '\n\n' + faq_md

    # 사진 마커는 워드프레스에서 실제 이미지를 넣을 자리 안내로 남긴다.
    combined_md = re.sub(
        r'\[사진\s*(\d+)\]',
        r'**[사진 \1 — 워드프레스 편집기에서 이 위치에 사진을 넣으세요]**',
        combined_md,
    )

    # 마크다운 → HTML
    try:
        import markdown as _md
        html = _md.markdown(
            combined_md,
            extensions=['tables', 'fenced_code', 'sane_lists', 'nl2br'],
        )
    except Exception as e:
        logger.warning(f'[WP] 마크다운 변환 실패, 평문 폴백: {e}')
        html = '<p>' + (combined_md.replace('\n\n', '</p><p>')
                        .replace('\n', '<br>')) + '</p>'

    return {
        'title':   title or '제목 없음',
        'excerpt': excerpt,
        'slug':    slug,
        'tags':    tags,
        'html':    html,
    }


# ── 발행 ─────────────────────────────────────────────────────

def create_google_post(supabase, brand_id: str, google_text: str, *,
                       status: str = 'draft', title_override: str | None = None) -> dict:
    """구글판 텍스트를 브랜드 전용 워드프레스에 글로 생성.

    supabase: 명시적 클라이언트 (Flask 라우트는 current_app.supabase,
      Celery 워커는 자체 create_client(...) 인스턴스를 넘긴다).
    반환: {ok, message, status, link, edit_link} — 실패해도 예외를 던지지 않고
      ok=False + message 로 반환(호출자가 그대로 사용자/로그에 노출 가능).
    """
    client = get_client_for_user(brand_id, supabase=supabase)
    if not client:
        return {'ok': False, 'message': '먼저 이 브랜드의 워드프레스를 연결해주세요.'}

    raw = (google_text or '').strip()
    if not raw:
        return {'ok': False, 'message': '발행할 구글(워드프레스)판 내용이 없습니다.'}

    if status not in ('draft', 'publish', 'pending'):
        status = 'draft'

    parsed = parse_google_post(raw)
    title = (title_override or parsed['title'] or '제목 없음').strip()[:200]

    tag_ids: list = []
    try:
        if parsed['tags']:
            tag_ids = client.resolve_tag_ids(parsed['tags'])
    except Exception as e:
        logger.warning(f'[WP] 태그 해석 실패(무시): {e}')

    try:
        post = client.create_post(
            title=title,
            content=parsed['html'],
            status=status,
            slug=parsed['slug'] or None,
            excerpt=parsed['excerpt'] or None,
            tag_ids=tag_ids or None,
        )
        mark_used(brand_id, supabase=supabase)
    except WordPressError as e:
        logger.warning(f'[WP] publish 실패 brand={brand_id}: {e}')
        mark_error(brand_id, str(e), supabase=supabase)
        return {'ok': False, 'message': friendly_error_message(e)}
    except Exception as e:
        logger.error(f'[WP] publish 예외 brand={brand_id}: {e}', exc_info=True)
        return {'ok': False, 'message': '발행 중 오류가 발생했습니다.'}

    post_id = post.get('id')
    edit_link = f'{client.site}/wp-admin/post.php?post={post_id}&action=edit' if post_id else None
    logger.info(f'[WP] 발행 완료 brand={brand_id} post={post_id} status={status}')
    return {
        'ok': True,
        'post_id': post_id,
        'status': post.get('status') or status,
        'link': post.get('link'),
        'edit_link': edit_link,
        'message': ('워드프레스에 초안으로 저장되었습니다.'
                   if status == 'draft' else '워드프레스에 발행되었습니다.'),
    }


def publish_existing_post(supabase, brand_id: str, post_id) -> dict:
    """이미 만들어진 글(주로 자동 저장된 초안)의 상태를 발행(publish)으로 전환.

    create_google_post로 새 글을 또 만드는 대신, 같은 글의 status만 바꿔
    중복 포스트가 생기지 않게 한다.
    """
    client = get_client_for_user(brand_id, supabase=supabase)
    if not client:
        return {'ok': False, 'message': '먼저 이 브랜드의 워드프레스를 연결해주세요.'}
    if not post_id:
        return {'ok': False, 'message': '발행할 글을 찾을 수 없습니다.'}

    try:
        post = client.update_post(post_id, status='publish')
        mark_used(brand_id, supabase=supabase)
    except WordPressError as e:
        logger.warning(f'[WP] go-live 실패 brand={brand_id} post={post_id}: {e}')
        mark_error(brand_id, str(e), supabase=supabase)
        return {'ok': False, 'message': friendly_error_message(e)}
    except Exception as e:
        logger.error(f'[WP] go-live 예외 brand={brand_id} post={post_id}: {e}', exc_info=True)
        return {'ok': False, 'message': '발행 중 오류가 발생했습니다.'}

    edit_link = f'{client.site}/wp-admin/post.php?post={post_id}&action=edit'
    logger.info(f'[WP] go-live 완료 brand={brand_id} post={post_id}')
    return {
        'ok': True,
        'post_id': post_id,
        'status': post.get('status') or 'publish',
        'link': post.get('link'),
        'edit_link': edit_link,
        'message': '워드프레스에 발행되었습니다.',
    }


# ── 완성본(글 + 본문 이미지 + 썸네일) 통째로 발행 ─────────────────

_MIME_EXT = {
    'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png',
    'image/webp': 'webp', 'image/gif': 'gif',
}


def _split_sections(text: str) -> dict:
    """구글판 라벨 텍스트 → {title, excerpt, slug, body, faq, tags} 원문 조각."""
    sections: dict = {}
    current = None
    buf: list = []

    def _flush():
        if current is not None:
            sections[current] = ('\n'.join(buf)).strip()

    for line in (text or '').splitlines():
        matched = False
        for pat, key in _WP_LABELS:
            m = pat.match(line)
            if m:
                _flush()
                current = key
                buf = [m.group(1)] if m.group(1).strip() else []
                matched = True
                break
        if not matched:
            if current is None:
                current = 'body'
            buf.append(line)
    _flush()
    return sections


def _figure_html(src: str, alt: str = '') -> str:
    alt = (alt or '').replace('"', '&quot;')
    return (f'<figure class="wp-block-image size-large">'
            f'<img src="{src}" alt="{alt}"/></figure>')


def _fetch_image_bytes(url: str) -> tuple:
    """URL 또는 data URL → (bytes, mime). 실패 시 예외."""
    if url.startswith('data:image/'):
        import base64 as _b64
        header, b64 = url.split(',', 1)
        mime = header.split(';')[0].split(':')[1] or 'image/jpeg'
        return _b64.b64decode(b64), mime
    r = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0', 'Referer': url})
    r.raise_for_status()
    mime = (r.headers.get('Content-Type') or 'image/jpeg').split(';')[0].strip()
    return r.content, mime


def _upload_image_to_wp(client, url: str, idx) -> dict | None:
    """이미지 URL 다운로드 → WP 미디어 업로드 → {id, source_url}. 실패 시 None."""
    try:
        content, mime = _fetch_image_bytes(url)
        ext = _MIME_EXT.get(mime, 'jpg')
        media = client.upload_media(f'maesil_{idx}.{ext}', content, mime)
        return {'id': media.get('id'), 'source_url': media.get('source_url')}
    except Exception as e:
        logger.warning('[WP] 이미지 업로드 실패 (%s): %s', str(url)[:60], e)
        return None


def create_full_post(supabase, brand_id: str, google_text: str, *,
                     body_image_urls=None, thumbnail_url: str | None = None,
                     status: str = 'draft', title_override: str | None = None) -> dict:
    """완성본을 브랜드 워드프레스에 발행 — 본문 이미지 삽입 + 썸네일 대표이미지.

    body_image_urls: 본문에 넣을 이미지 URL 목록(순서 유지, 썸네일 제외).
    thumbnail_url:   대표 이미지(featured)로 쓸 썸네일 URL.
    이미지들은 각 URL을 다운로드해 워드프레스 미디어 라이브러리에 올린 뒤 그 주소를 사용한다.
    실패해도 예외를 던지지 않고 {ok, message, ...} 로 반환.
    """
    client = get_client_for_user(brand_id, supabase=supabase)
    if not client:
        return {'ok': False, 'message': '먼저 이 브랜드의 워드프레스를 연결해주세요.'}

    raw = (google_text or '').strip()
    if not raw:
        return {'ok': False, 'message': '발행할 구글(워드프레스)판 내용이 없습니다.'}
    if status not in ('draft', 'publish', 'pending'):
        status = 'draft'

    sec = _split_sections(raw)
    title   = (title_override or (sec.get('title') or '').strip() or '제목 없음').strip()[:200]
    excerpt = (sec.get('excerpt') or '').strip()
    slug    = _slugify(sec.get('slug') or '')
    body_md = (sec.get('body') or '').strip() or raw
    faq_md  = (sec.get('faq') or '').strip()
    tags = [t.strip().lstrip('#')
            for t in re.split(r'[,\n·、]', sec.get('tags') or '') if t.strip()]

    # ── 본문 이미지 업로드 (썸네일 제외) ──────────────────────
    uploaded = []
    for i, u in enumerate(body_image_urls or []):
        if not u:
            continue
        r = _upload_image_to_wp(client, u, i)
        if r and r.get('source_url'):
            uploaded.append(r)

    # ── 본문 블록(\n\n) 사이에 이미지 균등 삽입 ────────────────
    blocks = [b for b in re.split(r'\n{2,}', body_md) if b.strip()]
    if uploaded and blocks:
        n = len(blocks)
        step = max(1, n // (len(uploaded) + 1))
        out, placed = [], 0
        for bi, blk in enumerate(blocks):
            out.append(blk)
            if placed < len(uploaded) and (bi + 1) % step == 0 and bi < n - 1:
                out.append(_figure_html(uploaded[placed]['source_url']))
                placed += 1
        while placed < len(uploaded):    # 남은 건 끝에
            out.append(_figure_html(uploaded[placed]['source_url']))
            placed += 1
        body_out = '\n\n'.join(out)
    else:
        body_out = body_md
        for img in uploaded:
            body_out += '\n\n' + _figure_html(img['source_url'])

    # FAQ 부착
    combined = body_out
    if faq_md:
        if not re.search(r'(?im)^#{1,3}\s*(faq|자주\s*묻는)', faq_md):
            combined += '\n\n## 자주 묻는 질문(FAQ)\n\n' + faq_md
        else:
            combined += '\n\n' + faq_md

    try:
        import markdown as _md
        html = _md.markdown(combined, extensions=['tables', 'fenced_code', 'sane_lists', 'nl2br'])
    except Exception as e:
        logger.warning('[WP] 마크다운 변환 실패, 평문 폴백: %s', e)
        html = '<p>' + combined.replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'

    # ── 썸네일 → 대표 이미지 ──────────────────────────────────
    featured_id = None
    if thumbnail_url:
        t = _upload_image_to_wp(client, thumbnail_url, 'thumb')
        if t and t.get('id'):
            featured_id = t['id']

    tag_ids = []
    try:
        if tags:
            tag_ids = client.resolve_tag_ids(tags)
    except Exception as e:
        logger.warning('[WP] 태그 해석 실패(무시): %s', e)

    try:
        post = client.create_post(
            title=title, content=html, status=status,
            slug=slug or None, excerpt=excerpt or None,
            tag_ids=tag_ids or None, featured_media=featured_id,
        )
        mark_used(brand_id, supabase=supabase)
    except WordPressError as e:
        logger.warning('[WP] 완성본 발행 실패 brand=%s: %s', brand_id, e)
        mark_error(brand_id, str(e), supabase=supabase)
        return {'ok': False, 'message': friendly_error_message(e)}
    except Exception as e:
        logger.error('[WP] 완성본 발행 예외 brand=%s: %s', brand_id, e, exc_info=True)
        return {'ok': False, 'message': '발행 중 오류가 발생했습니다.'}

    post_id = post.get('id')
    edit_link = f'{client.site}/wp-admin/post.php?post={post_id}&action=edit' if post_id else None
    logger.info('[WP] 완성본 발행 완료 brand=%s post=%s imgs=%d featured=%s',
                brand_id, post_id, len(uploaded), bool(featured_id))
    return {
        'ok': True,
        'post_id': post_id,
        'status': post.get('status') or status,
        'link': post.get('link'),
        'edit_link': edit_link,
        'images_uploaded': len(uploaded),
        'featured': bool(featured_id),
        'message': (f'완성본이 워드프레스에 {"초안으로 저장" if status == "draft" else "발행"}되었습니다. '
                    f'(본문 이미지 {len(uploaded)}장'
                    + (', 대표이미지 포함' if featured_id else '') + ')'),
    }
