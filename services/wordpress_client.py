"""워드프레스(자체 호스팅) REST API 클라이언트.

사용:
    client = WordPressClient(site_url, username, app_password)
    me   = client.verify()                          # GET /users/me
    post = client.create_post(title=..., content=..., status='draft')

인증: 워드프레스 '애플리케이션 비밀번호'를 이용한 HTTP Basic.
  발급: 워드프레스 [사용자 → 프로필 → 애플리케이션 비밀번호] → 이름 입력 → 생성.
        'xxxx xxxx xxxx xxxx xxxx xxxx' 형태 (24자, 공백 포함) 1회 노출.

설계:
- 정상 응답 시 dict/list 반환, 실패 시 WordPressError 발생.
- 퍼머링크(고유주소)가 꺼진 사이트는 /wp-json/... 경로가 404 → ?rest_route= 폴백.
  verify() 성공 시점의 방식을 self.use_rest_route 에 기록해 이후 호출에 재사용.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20.0


class WordPressError(Exception):
    """워드프레스 API 호출 실패.

    Attributes:
        code:   워드프레스 표준 에러 코드 ('rest_no_route', 'rest_cannot_create', ...)
                또는 클라이언트 측 코드 ('timeout', 'network', 'invalid_response')
        status: HTTP 상태 코드 (네트워크 실패 시 0)
        detail: 사람이 읽을 수 있는 메시지
    """

    def __init__(self, code: str, status: int, detail: str = ''):
        super().__init__(f'{code} ({status}): {detail}' if detail else f'{code} ({status})')
        self.code = code
        self.status = status
        self.detail = detail


class WordPressClient:
    def __init__(
        self,
        site_url: str,
        username: str,
        app_password: str,
        *,
        use_rest_route: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        if not site_url or not username or not app_password:
            raise ValueError('site_url, username, app_password are required')
        self.site = site_url.rstrip('/')
        self.username = username
        # 앱 비밀번호는 공백을 포함해 발급되며 워드프레스가 공백을 무시하지만,
        # 앞뒤 공백만 제거하고 내부 공백은 그대로 보존한다(둘 다 정상 동작).
        self.app_password = app_password.strip()
        self.use_rest_route = use_rest_route
        self.timeout = timeout

    # ── 내부 ──────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        """REST 경로 → 절대 URL. 퍼머링크 방식에 따라 두 형태."""
        if self.use_rest_route:
            return f'{self.site}/?rest_route=/wp/v2{path}'
        return f'{self.site}/wp-json/wp/v2{path}'

    def _maybe_upgrade_site(self, r) -> bool:
        """응답이 리다이렉트를 거쳤고 최종 호스트가 바뀌었으면(주로 http→https)
        self.site 를 그 호스트로 승격하고 True 반환.

        POST 가 http→https 301 리다이렉트로 GET 이 되어 컬렉션(list)을 받는 문제를
        근본 차단한다. (requests 는 301/302 에서 POST 를 GET 으로 바꾸며 본문도 버림)
        """
        if not getattr(r, 'history', None):
            return False
        from urllib.parse import urlsplit
        p = urlsplit(r.url)
        new_site = f'{p.scheme}://{p.netloc}' if (p.scheme and p.netloc) else ''
        if new_site and new_site != self.site:
            logger.info('[WP] 리다이렉트 감지 → site 승격 %s → %s', self.site, new_site)
            self.site = new_site
            return True
        return False

    def _raw_request(self, method: str, path: str, *,
                     params: dict | None = None, json_body: dict | None = None,
                     _redirected: bool = False):
        url = self._url(path)
        try:
            r = requests.request(
                method, url,
                params=params or {},
                json=json_body,
                auth=(self.username, self.app_password),
                headers={'Accept': 'application/json'},
                timeout=self.timeout,
            )
        except requests.Timeout:
            raise WordPressError('timeout', 0, 'API 응답 시간 초과')
        except requests.RequestException as e:
            raise WordPressError('network', 0, str(e)[:200])

        # 리다이렉트(http→https 등)가 POST를 GET으로 바꿔 컬렉션(list)을 반환하는 문제 차단:
        # 최종 호스트로 site 승격 후 메서드 유지해 1회 재요청.
        if (not _redirected) and method.upper() != 'GET' and self._maybe_upgrade_site(r):
            return self._raw_request(method, path, params=params, json_body=json_body,
                                     _redirected=True)

        if r.status_code >= 400:
            code, detail = 'http_error', ''
            try:
                body = r.json()
                # 워드프레스 오류는 {"code": "...", "message": "..."} 형태
                code   = body.get('code')    or 'http_error'
                detail = body.get('message') or ''
            except ValueError:
                detail = (r.text or '')[:200]
            raise WordPressError(code, r.status_code, detail)

        try:
            return r.json()
        except ValueError:
            raise WordPressError('invalid_response', r.status_code, 'non-JSON response')

    def _request(self, method: str, path: str, *,
                 params: dict | None = None, json_body: dict | None = None):
        """_raw_request + 자가치유.

        쓰기(POST/PUT/PATCH/DELETE) 응답이 list 로 오면 rest_route 방식이 잘못
        잡힌 것(퍼머링크 켜진 사이트인데 ?rest_route= 로 POST 가 컬렉션으로
        오라우팅)이므로, 경로 방식을 뒤집어 1회 재시도한다.
        GET 의 list(목록 응답)는 정상이므로 건드리지 않는다.
        """
        result = self._raw_request(method, path, params=params, json_body=json_body)
        if method.upper() != 'GET' and isinstance(result, list):
            logger.info('[WP] %s %s 응답이 list — rest_route 모드 전환 재시도', method, path)
            self.use_rest_route = not self.use_rest_route
            result = self._raw_request(method, path, params=params, json_body=json_body)
        return result

    # ── 엔드포인트 ────────────────────────────────────────────
    def verify(self) -> dict:
        """GET /users/me — 인증 검증 + 사용자 정보.

        퍼머링크가 꺼진 사이트는 /wp-json 경로가 실패할 수 있어,
        인증(401/403) 이 아닌 오류면 ?rest_route= 방식으로 1회 재시도한다.
        """
        try:
            return self._request('GET', '/users/me', params={'context': 'edit'})
        except WordPressError as e:
            if e.status not in (401, 403) and not self.use_rest_route:
                logger.info('[WP] 퍼머링크 경로 실패(%s) → rest_route 폴백 재시도', e.status)
                self.use_rest_route = True
                return self._request('GET', '/users/me', params={'context': 'edit'})
            raise

    def create_post(
        self,
        *,
        title: str,
        content: str,
        status: str = 'draft',
        slug: str | None = None,
        excerpt: str | None = None,
        tag_ids: list | None = None,
        featured_media: int | None = None,
    ) -> dict:
        """POST /posts — 글 생성. 기본은 초안(draft).

        featured_media: 대표 이미지 미디어 ID (upload_media 결과의 'id').
        """
        body: dict = {'title': title, 'content': content, 'status': status}
        if slug:
            body['slug'] = slug
        if excerpt:
            body['excerpt'] = excerpt
        if tag_ids:
            body['tags'] = tag_ids
        if featured_media:
            body['featured_media'] = featured_media
        return self._request('POST', '/posts', json_body=body)

    def update_post(self, post_id, *, status: str | None = None,
                    title: str | None = None, content: str | None = None,
                    featured_media: int | None = None) -> dict:
        """POST /posts/<id> — 기존 글 부분 수정. 주로 초안 → 발행 전환에 사용
        (create_post로 새로 만들지 않고 같은 글의 상태만 바꿔 중복 포스트 방지)."""
        body: dict = {}
        if status is not None:
            body['status'] = status
        if title is not None:
            body['title'] = title
        if content is not None:
            body['content'] = content
        if featured_media:
            body['featured_media'] = featured_media
        return self._request('POST', f'/posts/{post_id}', json_body=body)

    def upload_media(self, filename: str, content: bytes,
                     mime: str = 'image/jpeg') -> dict:
        """POST /media — 이미지 바이너리 업로드. list 응답(rest_route 오라우팅)이면
        경로 방식을 뒤집어 1회 재시도한다."""
        media = self._do_upload_media(filename, content, mime)
        if isinstance(media, list):
            logger.info('[WP] /media 응답이 list — rest_route 모드 전환 재시도')
            self.use_rest_route = not self.use_rest_route
            media = self._do_upload_media(filename, content, mime)
        return media

    def _do_upload_media(self, filename: str, content: bytes,
                         mime: str = 'image/jpeg', _redirected: bool = False) -> dict:
        """POST /media — 이미지 바이너리를 미디어 라이브러리에 업로드.

        _request 는 JSON 전용이라, 바이너리 업로드는 별도로 처리한다.
        반환: {'id': int, 'source_url': str, ...} (WP 미디어 객체).
        """
        url = self._url('/media')
        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': mime or 'application/octet-stream',
            'Accept': 'application/json',
        }
        try:
            r = requests.post(
                url, data=content, headers=headers,
                auth=(self.username, self.app_password),
                timeout=max(self.timeout, 60),   # 업로드는 넉넉히
            )
        except requests.Timeout:
            raise WordPressError('timeout', 0, '미디어 업로드 시간 초과')
        except requests.RequestException as e:
            raise WordPressError('network', 0, str(e)[:200])

        if (not _redirected) and self._maybe_upgrade_site(r):
            return self._do_upload_media(filename, content, mime, _redirected=True)

        if r.status_code >= 400:
            code, detail = 'http_error', ''
            try:
                body = r.json()
                code   = body.get('code')    or 'http_error'
                detail = body.get('message') or ''
            except ValueError:
                detail = (r.text or '')[:200]
            raise WordPressError(code, r.status_code, detail)

        try:
            return r.json()
        except ValueError:
            raise WordPressError('invalid_response', r.status_code, 'non-JSON response')

    def _search_tag_id(self, name: str):
        """GET /tags?search= → 이름이 정확히 일치하는 태그 ID (없으면 None)."""
        found = self._request('GET', '/tags', params={'search': name, 'per_page': 20})
        for t in (found or []):
            if isinstance(t, dict) and (t.get('name') or '').strip().lower() == name.lower():
                return t.get('id')
        return None

    def resolve_tag_ids(self, names: list) -> list:
        """태그 이름 목록 → 태그 ID 목록 (없으면 생성). best-effort — 실패는 건너뜀."""
        ids: list = []
        for raw in (names or [])[:10]:
            name = (raw or '').strip().lstrip('#').strip()
            if not name:
                continue
            try:
                tid = self._search_tag_id(name)          # ① 기존 태그 검색
                if tid is None:
                    try:                                  # ② 없으면 생성
                        created = self._request('POST', '/tags', json_body={'name': name})
                        if isinstance(created, dict):
                            tid = created.get('id')
                    except WordPressError as e:
                        # 이미 존재(term_exists)하거나 검색이 놓친 경우 → 재검색으로 확보
                        if e.code == 'term_exists' or e.status == 400:
                            tid = self._search_tag_id(name)
                        else:
                            raise
                if tid:
                    ids.append(tid)
                else:
                    logger.warning('[WP] 태그 확보 실패(%s) — 검색·생성 모두 미해결', name)
            except WordPressError as e:
                logger.warning('[WP] 태그 처리 실패(%s): %s', name, e)
                continue
        logger.info('[WP] 태그 해석 %d/%d개 성공', len(ids), len([n for n in (names or []) if (n or '').strip()]))
        return ids


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────

def friendly_error_message(err: WordPressError) -> str:
    """WordPressError → 사용자에게 표시할 한국어 메시지."""
    if err.status == 401:
        return ('아이디 또는 애플리케이션 비밀번호가 올바르지 않습니다. '
                '워드프레스 [프로필 → 애플리케이션 비밀번호]에서 발급한 값인지 확인해주세요.')
    if err.status == 403:
        return '이 계정은 글 작성 권한이 없습니다. (편집자 이상 권한 필요)'

    msgs = {
        'timeout':          '워드프레스 서버 응답이 지연됩니다. 잠시 후 다시 시도해주세요.',
        'network':          '워드프레스 사이트에 연결할 수 없습니다. 사이트 주소(https 포함)를 확인해주세요.',
        'invalid_response': ('워드프레스 REST API 응답을 해석할 수 없습니다. '
                             'REST API가 활성화돼 있는지, 보안 플러그인이 막고 있지 않은지 확인해주세요.'),
        'rest_no_route':    'REST API 경로를 찾을 수 없습니다. 워드프레스 [설정 → 고유주소]를 확인해주세요.',
        'rest_cannot_create': '이 계정은 글을 작성할 권한이 없습니다.',
    }
    base = msgs.get(err.code)
    if base:
        return base
    if err.detail:
        return f'워드프레스 오류: {err.detail}'
    return '워드프레스 연동 중 오류가 발생했습니다.'
