"""상품 URL 이미지/정보 추출
쿠팡, 네이버 스마트스토어, 일반 쇼핑몰 OG 태그 기반 추출
"""
import logging
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if 'coupang.com' in host:
        return 'coupang'
    if 'smartstore.naver.com' in host or 'shopping.naver.com' in host:
        return 'naver'
    return 'general'


def fetch_product_info(url: str) -> dict:
    """URL → 상품명, 설명, 이미지 목록 추출"""
    platform = detect_platform(url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        raise ValueError(f'페이지를 불러올 수 없습니다: {e}')

    result = {
        'platform': platform,
        'url': url,
        'name': '',
        'description': '',
        'price': None,
        'images': [],
    }

    # ── OG 태그 우선 추출 ──────────────────────────────
    og_title = _og(soup, 'og:title') or _og(soup, 'title')
    og_desc  = _og(soup, 'og:description') or _og(soup, 'description')
    og_image = _og(soup, 'og:image')

    result['name']        = _clean(og_title)
    result['description'] = _clean(og_desc)
    if og_image:
        result['images'].append(_normalize_image_url(og_image, url))

    # ── 플랫폼별 추가 이미지 추출 ──────────────────────
    if platform == 'coupang':
        result['images'] += _coupang_images(soup, url)
    elif platform == 'naver':
        result['images'] += _naver_images(soup, url)
    else:
        result['images'] += _general_images(soup, url)

    # 중복 제거
    seen, unique = set(), []
    for img in result['images']:
        if img and img not in seen:
            seen.add(img)
            unique.append(img)
    result['images'] = unique[:20]  # 최대 20장

    # 가격 추출
    result['price'] = _extract_price(soup, platform)

    return result


# ── OG 태그 ────────────────────────────────────────────
def _og(soup, prop: str) -> str:
    tag = (soup.find('meta', property=prop)
           or soup.find('meta', attrs={'name': prop}))
    return tag.get('content', '').strip() if tag else ''


def _clean(text: str) -> str:
    if not text:
        return ''
    # 쇼핑몰명 suffix 제거 (예: "상품명 | 쿠팡")
    text = re.sub(r'\s*[|\-–]\s*(쿠팡|네이버|스마트스토어|Coupang).*$', '', text, flags=re.I)
    return text.strip()


# ── 쿠팡 이미지 ────────────────────────────────────────
def _coupang_images(soup, base_url: str) -> list:
    imgs = []
    # 상품 상세 이미지 영역
    for sel in ['#product-detail img', '.prod-image__detail img', '.detail-image img']:
        for tag in soup.select(sel):
            src = tag.get('src') or tag.get('data-src', '')
            if src:
                imgs.append(_normalize_image_url(src, base_url))
    # 일반 img 태그 중 큰 것
    if not imgs:
        imgs = _general_images(soup, base_url)
    return imgs


# ── 네이버 스마트스토어 이미지 ──────────────────────────
def _naver_images(soup, base_url: str) -> list:
    imgs = []
    for sel in ['.product_img img', '._3xSAg img', '.detail_img img']:
        for tag in soup.select(sel):
            src = tag.get('src') or tag.get('data-src', '')
            if src:
                imgs.append(_normalize_image_url(src, base_url))
    if not imgs:
        imgs = _general_images(soup, base_url)
    return imgs


# ── 일반 쇼핑몰 이미지 ─────────────────────────────────
def _general_images(soup, base_url: str) -> list:
    imgs = []
    for tag in soup.find_all('img'):
        src = tag.get('src') or tag.get('data-src') or tag.get('data-original', '')
        if not src:
            continue
        # 작은 아이콘/로고 제외 (width/height 힌트)
        w = tag.get('width', '9999')
        h = tag.get('height', '9999')
        try:
            if int(str(w).replace('px', '')) < 200:
                continue
        except Exception:
            pass
        imgs.append(_normalize_image_url(src, base_url))
    return imgs[:15]


# ── 가격 추출 ──────────────────────────────────────────
def _extract_price(soup, platform: str):
    patterns = [
        r'(\d{1,3}(?:,\d{3})+)원',
        r'₩\s*(\d{1,3}(?:,\d{3})+)',
    ]
    text = soup.get_text()
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                return int(m.group(1).replace(',', ''))
            except Exception:
                pass
    return None


# ── URL 정규화 ─────────────────────────────────────────
def _normalize_image_url(src: str, base_url: str) -> str:
    src = src.strip()
    if src.startswith('//'):
        return 'https:' + src
    if src.startswith('http'):
        return src
    if src.startswith('/'):
        parsed = urlparse(base_url)
        return f'{parsed.scheme}://{parsed.netloc}{src}'
    return src
