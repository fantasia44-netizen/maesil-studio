# 매실 인사이트 — 외부 상품 API 추가 설계 요청서 v1

> **요청자**: 매실 스튜디오 (maesil-studio / 매실 크리에이터)
> **수신자**: 매실 인사이트 (maesil-insight)
> **작성일**: 2026-04-29
> **목적**: 매실 스튜디오가 매실 인사이트의 상품 데이터를 조회하여 콘텐츠(블로그/이미지/광고카피) 자동 생성에 활용할 수 있도록, 외부에 노출되는 상품 조회 API를 신규 추가 요청.

---

## 1. 배경 (Why)

매실 스튜디오는 브랜드/상품 기반 콘텐츠 자동 생성 SaaS입니다. 사용자가 본인의 상품 정보를 직접 입력하는 마찰을 줄이기 위해, **매실 인사이트 사용 운영사는 API로 상품 정보를 자동 동기화**할 수 있도록 합니다.

이 연동의 효과:
- 매실 스튜디오: 상품 등록 마찰 제거 → 활성 사용률 ↑
- 매실 인사이트: 락인 효과 ↑ (인사이트 → 스튜디오 cross-sell)
- 양 서비스 패밀리 전체: 상품 데이터를 마스터(매실 인사이트)에 단일 소스로 유지

---

## 2. 사용 시나리오

```
[매실 인사이트 사용자]
  └─ 매실 인사이트 [설정 → 외부 연동] 진입
  └─ "매실 스튜디오 연동용 토큰 발급" 클릭
  └─ 토큰 1회 노출 (복사) — 이후 마스킹

[매실 스튜디오로 이동]
  └─ [설정 → 외부 연동 → 매실 인사이트] 입력
  └─ 토큰 붙여넣기 + "연결 확인" 클릭
       (스튜디오 → 인사이트 GET /api/v1/external/me 호출하여 검증)
  └─ 연결 성공 시 operator 정보 캐시

[상품 등록]
  └─ "매실 인사이트에서 가져오기" 옵션 노출
  └─ 스튜디오 → 인사이트 GET /api/v1/external/products 호출
  └─ 사용자가 가져올 상품 선택 → 스튜디오 products 테이블에 저장
       (source='maesil_insight', source_ref=seller_product_id)
```

---

## 3. 인증 모델

### 3.1 토큰 방식

- **Bearer 토큰 (HTTP Authorization 헤더)**
- **operator 단위**로 발급 (사용자 단위가 아님 — 멀티테넌트 모델에 맞춤)
- **scope** 필드 포함 — 향후 권한 분리 대비
- **만료일** 선택적 (기본 1년 / 무기한)

### 3.2 헤더 형식

```http
Authorization: Bearer mi_<랜덤32자>
X-Source: maesil-creator
```

- `mi_` 접두사로 매실 인사이트 발급 토큰임을 식별 (선택)
- `X-Source` 헤더는 호출 프로그램 식별 (기존 `notify/email` 패턴과 동일)

### 3.3 저장 방식

토큰 원문은 발급 시 1회만 노출. DB에는 **bcrypt 또는 SHA-256 해시**로 저장. 검증 시 입력 토큰을 같은 방식으로 해싱하여 비교.

---

## 4. API 엔드포인트

모든 응답은 JSON. 시간 필드는 ISO 8601 (KST 권장) 또는 timestamptz 문자열.

### 4.1 `GET /api/v1/external/me`

**목적**: 토큰 검증 + operator 정보 반환 (스튜디오 측 연결 확인용)

**응답 200**
```json
{
  "operator_id": "fa2ea8de-be49-40d1-81a0-23d27022e87f",
  "operator_name": "배마마",
  "plan": "professional",
  "scopes": ["products:read"],
  "issued_at": "2026-04-15T10:30:00+09:00",
  "expires_at": "2027-04-15T10:30:00+09:00"
}
```

**응답 401**
```json
{ "error": "unauthorized", "detail": "invalid or expired token" }
```

---

### 4.2 `GET /api/v1/external/products`

**목적**: operator의 상품 목록 조회 (페이지네이션 + 검색)

**쿼리 파라미터**
| 키 | 타입 | 기본 | 설명 |
|----|------|------|------|
| `page` | int | 1 | 페이지 번호 (1-indexed) |
| `per_page` | int | 50 | 페이지당 개수 (최대 100) |
| `keyword` | string | - | 상품명 검색 (대소문자 무시) |
| `category` | string | - | 카테고리 필터 |
| `channel` | string | - | 채널 필터 (`naver`, `coupang`, `coupang_rocket`, `cafe24`, `11st`, `gmarket`, `auction`, `kakao`) |
| `status` | string | `active` | `active` | `all` |
| `sort` | string | `recent` | `recent` | `name` | `sales_30d_desc` |

**응답 200**
```json
{
  "products": [
    {
      "seller_product_id": "12345678",
      "display_name": "배마마 야채큐브 30개입 (당근/브로콜리/애호박/단호박)",
      "category": "이유식",
      "sale_price": 18900,
      "channels": ["naver", "coupang"],
      "primary_channel": "naver",
      "primary_channel_product_no": "12345678",
      "image_url": "https://shopping-phinf.pstatic.net/...",
      "status": "active",
      "last_synced_at": "2026-04-29T03:15:00+09:00",
      "options": [
        { "name": "30개입", "sale_price": 18900 },
        { "name": "60개입", "sale_price": 35000 }
      ]
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 127,
    "total_pages": 3
  }
}
```

**응답 401 / 403 / 429**: 표준 에러 형식 (아래 §7)

---

### 4.3 `GET /api/v1/external/products/{seller_product_id}`

**목적**: 단일 상품 상세 조회 (콘텐츠 생성 컨텍스트용)

**응답 200**
```json
{
  "seller_product_id": "12345678",
  "display_name": "배마마 야채큐브 30개입",
  "category": "이유식",
  "sale_price": 18900,
  "channels": ["naver", "coupang"],
  "primary_channel": "naver",
  "image_url": "https://...",
  "image_urls": ["https://...", "https://..."],
  "status": "active",
  "options": [...],

  "features": [
    "첨가물 무첨가",
    "월령별 큐브",
    "급속 냉동"
  ],
  "description_summary": "...",

  "insights": {
    "sales_30d": 234,
    "sales_90d": 612,
    "trend": "up",
    "avg_review_score": 4.8,
    "review_count": 1245,
    "top_review_keywords": ["편리해요", "신선함", "양 많음"]
  },

  "last_synced_at": "2026-04-29T03:15:00+09:00"
}
```

> **참고**: `features`, `description_summary`, `insights`는 인사이트 측에서 산출 가능한 범위에서 채워주시면 되며, 데이터 부재 시 필드 자체를 생략하거나 `null` 반환 모두 무방. 매실 스튜디오 측은 둘 다 안전하게 처리합니다.

---

### 4.4 `GET /api/v1/external/categories` (선택 — 있으면 좋음)

**목적**: operator가 보유한 카테고리 목록 (스튜디오 필터용)

**응답 200**
```json
{
  "categories": [
    { "key": "이유식", "product_count": 12 },
    { "key": "건강식품", "product_count": 5 }
  ]
}
```

---

## 5. DB 변경

### 5.1 신규 테이블: `external_api_tokens`

```sql
CREATE TABLE IF NOT EXISTS external_api_tokens (
  id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  operator_id   uuid NOT NULL REFERENCES operators(id) ON DELETE CASCADE,
  token_hash    text NOT NULL,                    -- SHA-256 또는 bcrypt
  token_prefix  text NOT NULL,                    -- 식별용 앞 8자 (예: 'mi_a3f2...')
  label         text NOT NULL DEFAULT '',         -- 사용자 메모 ('매실 스튜디오 연동' 등)
  source        text NOT NULL,                    -- 'maesil-creator' | (향후 확장)
  scopes        text[] NOT NULL DEFAULT ARRAY['products:read'],
  expires_at    timestamptz,                      -- NULL = 무기한
  last_used_at  timestamptz,
  is_revoked    boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  revoked_at    timestamptz
);

CREATE INDEX idx_external_api_tokens_operator
  ON external_api_tokens(operator_id);

CREATE INDEX idx_external_api_tokens_hash
  ON external_api_tokens(token_hash) WHERE NOT is_revoked;
```

### 5.2 기존 테이블 변경 없음

`product_costs`, `my_products` 등은 그대로 사용. 필요 시 외부 노출용 view를 만들어도 좋습니다 (예: `vw_external_products`).

---

## 6. UI 변경

### 6.1 신규 페이지: `/settings/integrations` (또는 적절한 위치)

- 토큰 목록 테이블
  - 컬럼: 라벨 / 출처 / prefix / 발급일 / 마지막 사용 / 만료일 / 상태(활성/만료/취소) / 작업(취소)
- "새 토큰 발급" 버튼
  - 모달: 라벨 입력 + scope 선택(현재 `products:read` 고정) + 만료(1년/무기한)
  - 발급 직후 토큰 원문 1회 노출 + "복사" 버튼 + "다시 볼 수 없습니다" 경고
  - 발급 후 모달 닫으면 토큰 원문은 영구 비공개

### 6.2 어드민 (선택)

- `/admin/integrations` — 전체 발급 토큰/사용량 모니터링
- 의심스러운 사용 (예: 분당 1000+ 호출) 자동 알림

---

## 7. 표준 에러 형식

```json
{ "error": "<error_code>", "detail": "<human_readable_message>" }
```

| HTTP | error_code | 의미 |
|------|-----------|------|
| 400 | `invalid_request` | 파라미터 형식 오류 |
| 401 | `unauthorized` | 토큰 없음/만료/취소 |
| 403 | `forbidden` | 스코프 부족 |
| 404 | `not_found` | 리소스 없음 |
| 429 | `rate_limited` | 레이트리밋 초과 (`Retry-After` 헤더 동봉) |
| 500 | `internal_error` | 서버 오류 |
| 503 | `service_unavailable` | DB 다운 등 일시 장애 |

---

## 8. 레이트리밋 / 보안

### 8.1 레이트리밋
- 토큰당 분당 60회, 시간당 1000회 권장
- 초과 시 429 + `Retry-After` 헤더
- Redis가 있으면 Redis 카운터, 없으면 메모리 폴백 (기존 매실 인사이트 패턴 참고)

### 8.2 감사 로그
- 모든 외부 API 호출은 별도 로그 테이블에 기록 권장
  ```sql
  CREATE TABLE IF NOT EXISTS external_api_logs (
    id BIGSERIAL PRIMARY KEY,
    token_id      uuid REFERENCES external_api_tokens(id) ON DELETE SET NULL,
    operator_id   uuid,
    method        text,
    path          text,
    status_code   int,
    duration_ms   int,
    ip            text,
    user_agent    text,
    created_at    timestamptz DEFAULT now()
  );
  CREATE INDEX idx_external_api_logs_op ON external_api_logs(operator_id, created_at DESC);
  ```
- 비정상 패턴 감지 (다른 IP 다발 호출, 24시간 호출 0건 후 갑자기 1000건 등)

### 8.3 토큰 발급 보호
- CSRF 토큰 (Flask-WTF)
- 토큰 발급 시 사용자에게 비밀번호 재확인 권장 (선택)
- 토큰 노출은 **1회만** — 재조회 불가

### 8.4 응답 데이터 격리
- 모든 쿼리는 `WHERE operator_id = <token_owner_operator>` 강제
- 토큰의 operator_id로 강제 필터, 쿼리 파라미터의 operator_id 무시 (보안)

---

## 9. 수용 기준 (Acceptance Criteria)

구현 완료 판정을 위한 체크리스트:

### 9.1 기능
- [ ] `GET /api/v1/external/me` — 유효 토큰 → 200 + operator 정보, 무효 토큰 → 401
- [ ] `GET /api/v1/external/products` — 페이지네이션, keyword/category/channel/status 필터 작동
- [ ] `GET /api/v1/external/products/{seller_product_id}` — 정상/404/타 operator 상품 → 404 (정보 누설 방지)
- [ ] 모든 응답이 JSON 스키마 일치
- [ ] 토큰 발급 UI에서 토큰 원문 1회만 노출, 이후 마스킹 (`mi_a3f2****`)
- [ ] 토큰 취소 시 즉시 401 반환

### 9.2 보안
- [ ] 토큰 원문이 DB·로그·응답·예외스택에 어디에도 평문 저장 안 됨
- [ ] 다른 operator의 상품 ID로 조회 시 404 반환 (403 아님 — 존재 여부 누설 방지)
- [ ] CSRF 토큰 검증 (토큰 발급/취소 폼)
- [ ] 토큰 prefix만으로는 토큰 추측 불가 (충분한 엔트로피)

### 9.3 성능
- [ ] 100개 상품 보유 operator 기준 `GET /products` p95 < 500ms
- [ ] 레이트리밋 정상 작동 (분당 60회 초과 → 429)

### 9.4 모니터링
- [ ] external_api_logs에 모든 호출 기록
- [ ] 어드민 대시보드에 토큰별 사용량 통계 노출 (선택)

---

## 10. 비범위 (Out of Scope — 이번 요청에 포함되지 않음)

- 상품 **쓰기**(생성/수정/삭제) API — 이번엔 읽기 전용
- 주문/정산 데이터 노출 — 향후 필요 시 별도 요청
- OAuth 2.0 — 현 단계에선 단순 Bearer 토큰. 향후 외부 파트너 확대 시 OAuth 도입 검토
- 웹훅 (상품 변경 시 push) — Phase 2

---

## 11. 매실 스튜디오 측 호출 예시 (참고)

구현하시는 분이 클라이언트 동작을 이해하시는 데 도움이 되도록:

```python
# services/maesil_insight_client.py (매실 스튜디오 측)
import requests

class MaesilInsightClient:
    BASE_URL = 'https://maesil-insight.com/api/v1/external'

    def __init__(self, token: str):
        self.headers = {
            'Authorization': f'Bearer {token}',
            'X-Source': 'maesil-creator',
        }

    def verify(self) -> dict:
        r = requests.get(f'{self.BASE_URL}/me', headers=self.headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def list_products(self, page=1, per_page=50, **filters) -> dict:
        params = {'page': page, 'per_page': per_page, **filters}
        r = requests.get(f'{self.BASE_URL}/products',
                         headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def get_product(self, seller_product_id: str) -> dict:
        r = requests.get(f'{self.BASE_URL}/products/{seller_product_id}',
                         headers=self.headers, timeout=10)
        r.raise_for_status()
        return r.json()
```

---

## 12. 일정 / 우선순위

매실 스튜디오 측은 이 API 완성을 기다리지 않고도 다음 작업을 병렬 진행 가능:
- DB 마이그레이션 (products.avoid_words/source/source_ref 등)
- 시스템 금지어 + 디스클레이머
- 블로그 폼 4축 재구조화

매실 인사이트 측 작업은 약 **3~5일** 예상 (토큰 발급 UI 포함). 우선순위는 매실 인사이트 팀에서 자율 판단.

---

## 13. 문의

설계상 의문/충돌이 있으면 매실 스튜디오 세션으로 회신 부탁드립니다. 특히 다음 항목은 인사이트 측 데이터 가용성에 따라 조정 가능:
- §4.3의 `insights` 필드 (sales_30d, top_review_keywords 등) 산출 가능 범위
- §4.2의 `sort=sales_30d_desc` 지원 여부
- 토큰 발급 UI 위치 (기존 `/settings` 하위 vs 신규 `/integrations`)

설계 합의 후 본 문서를 v1.1로 업데이트하여 구현 착수 권장.
