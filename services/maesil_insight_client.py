"""매실 인사이트 외부 API 클라이언트.

사용:
    client = MaesilInsightClient(token)
    me = client.verify()                       # GET /me
    page = client.list_products(page=1, ...)   # GET /products
    p = client.get_product('12345678')         # GET /products/<id>
    cats = client.categories()                 # GET /categories

토큰 발급: 인사이트 [설정 → 외부 API 연동] → 'mi_<32자>' 1회 노출.

설계:
- 모든 메서드는 정상 응답 시 dict 반환, 실패 시 MaesilInsightError 발생.
- 타임아웃 기본 15초, 호출자가 override 가능.
- 응답이 JSON 이 아니거나 4xx/5xx 면 error_code/status/detail 정규화.
- base URL 은 환경변수 MAESIL_INSIGHT_BASE 로 override (테스트/스테이징).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE = os.environ.get(
    'MAESIL_INSIGHT_BASE',
    'https://maesil-insight.com/api/v1/external',
)
DEFAULT_TIMEOUT = 15.0


class MaesilInsightError(Exception):
    """인사이트 API 호출 실패.

    Attributes:
        code:   인사이트 표준 에러 코드 ('unauthorized', 'rate_limited', ...)
                또는 클라이언트 측 코드 ('timeout', 'network', 'invalid_response')
        status: HTTP 상태 코드 (네트워크 실패 시 0)
        detail: 사람이 읽을 수 있는 메시지
    """

    def __init__(self, code: str, status: int, detail: str = ''):
        super().__init__(f'{code} ({status}): {detail}' if detail else f'{code} ({status})')
        self.code = code
        self.status = status
        self.detail = detail


# ─────────────────────────────────────────────────────────────
# 클라이언트
# ─────────────────────────────────────────────────────────────

class MaesilInsightClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        if not token:
            raise ValueError('token is required')
        self.token = token
        self.base = (base_url or DEFAULT_BASE).rstrip('/')
        self.timeout = timeout
        self.headers = {
            'Authorization': f'Bearer {token}',
            'X-Source':      'maesil-creator',
            'Accept':        'application/json',
        }

    # ── 내부 ──────────────────────────────────────────────────
    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f'{self.base}{path}'
        try:
            r = requests.get(url, headers=self.headers, params=params or {},
                             timeout=self.timeout)
        except requests.Timeout:
            raise MaesilInsightError('timeout', 0, 'API 응답 시간 초과')
        except requests.RequestException as e:
            raise MaesilInsightError('network', 0, str(e)[:200])

        if r.status_code >= 400:
            code, detail = 'http_error', ''
            try:
                body = r.json()
                code   = body.get('error')  or 'http_error'
                detail = body.get('detail') or ''
            except ValueError:
                detail = (r.text or '')[:200]
            raise MaesilInsightError(code, r.status_code, detail)

        try:
            return r.json()
        except ValueError:
            raise MaesilInsightError(
                'invalid_response', r.status_code, 'non-JSON response')

    # ── 엔드포인트 ────────────────────────────────────────────
    def verify(self) -> dict:
        """GET /me — 토큰 검증 + operator 정보."""
        return self._get('/me')

    def list_products(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        keyword: str | None = None,
        category: str | None = None,
        channel: str | None = None,
        status: str | None = None,
        sort: str | None = None,
    ) -> dict:
        """GET /products — 목록 + 페이지네이션."""
        params: dict[str, Any] = {'page': page, 'per_page': per_page}
        if keyword:  params['keyword']  = keyword
        if category: params['category'] = category
        if channel:  params['channel']  = channel
        if status:   params['status']   = status
        if sort:     params['sort']     = sort
        return self._get('/products', params)

    def get_product(self, seller_product_id: str) -> dict:
        """GET /products/<id> — 상세."""
        if not seller_product_id:
            raise ValueError('seller_product_id is required')
        return self._get(f'/products/{seller_product_id}')

    def categories(self) -> dict:
        """GET /categories — 카테고리 목록."""
        return self._get('/categories')


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────

def friendly_error_message(err: MaesilInsightError) -> str:
    """MaesilInsightError → 사용자에게 표시할 한국어 메시지."""
    code = err.code
    msgs = {
        'unauthorized':        '토큰이 유효하지 않거나 만료되었습니다. 인사이트에서 토큰을 다시 발급해주세요.',
        'forbidden':           '이 토큰은 상품 조회 권한이 없습니다.',
        'rate_limited':        '요청이 너무 많습니다. 잠시 후 다시 시도해주세요.',
        'not_found':           '해당 상품을 찾을 수 없습니다.',
        'invalid_request':     '요청 형식이 잘못되었습니다.',
        'timeout':             '인사이트 서버 응답이 지연됩니다. 잠시 후 다시 시도해주세요.',
        'network':             '네트워크 오류가 발생했습니다.',
        'invalid_response':    '인사이트 응답을 해석할 수 없습니다.',
        'internal_error':      '인사이트 서버 오류입니다.',
        'service_unavailable': '인사이트 서비스가 일시적으로 이용할 수 없습니다.',
    }
    base = msgs.get(code, '알 수 없는 오류가 발생했습니다.')
    if err.detail and code not in ('unauthorized', 'forbidden'):
        return f'{base} ({err.detail})'
    return base
