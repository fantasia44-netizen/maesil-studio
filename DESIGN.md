# 매실 브랜드 스튜디오 (Maesil Brand Studio)
> 상품 입력 → 브랜드 분석 → 콘텐츠 생성 → 판매 → 확산까지 자동화하는 커머스 SaaS

**슬로건:** "당신의 비즈니스를 브랜드로 완성하는 가장 공정한 데이터 엔진"

---

## 1. 제품 정의

### 핵심 플로우
```
상품 입력
  → 브랜드 분석 / 생성
  → 콘텐츠 자동 생성 (텍스트 + 이미지)
  → 판매 페이지 구성
  → 확산 전략 (인플루언서 DM · 제안서)
  → 성과 피드백
```

### 차별화 포인트
1. **상품 기반 자동 생성** — 상품명 + 특징 입력 시 전체 콘텐츠 세트 생성
2. **브랜드 일관성 유지** — 브랜드 프로필을 모든 생성에 컨텍스트로 주입
3. **한글 오타 0%** — 긴 텍스트는 AI 생성 대신 PIL 합성으로 처리
4. **매실 인사이트 연동 가능** — 실제 판매 데이터 피드백 루프 (Phase 3)

---

## 2. 기술 스택

| 레이어 | 기술 |
|--------|------|
| 서버 | Python 3.12 + Flask 3.1 + Gunicorn |
| DB | Supabase (PostgreSQL) + Storage |
| 인증 | Flask-Login + bcrypt + JWT |
| 텍스트 AI | Claude claude-sonnet-4-6 (Anthropic) |
| 이미지 AI | FLUX.2 via fal.ai + Ideogram 3.0 + PIL |
| 결제 | PortOne v2 (카드 + 카카오페이) |
| 배포 | Render (Singapore, Starter) |
| 세션 | filesystem (Redis 선택) |

---

## 3. DB 스키마

```sql
-- 1. uuid 확장
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. users — B2C 사용자
CREATE TABLE IF NOT EXISTS users (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  email               text UNIQUE NOT NULL,
  password_hash       text NOT NULL,
  name                text,
  phone               text,
  plan_type           text NOT NULL DEFAULT 'free',       -- free | starter | growth | pro
  is_active           boolean NOT NULL DEFAULT true,
  is_deleted          boolean NOT NULL DEFAULT false,
  site_role           text NOT NULL DEFAULT 'user',       -- user | superadmin
  failed_login_count  int NOT NULL DEFAULT 0,
  locked_until        timestamptz,
  last_login_at       timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_email ON users(email);

-- 3. subscriptions
CREATE TABLE IF NOT EXISTS subscriptions (
  id                    uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_type             text NOT NULL,
  status                text NOT NULL DEFAULT 'trial',    -- trial | active | past_due | cancelled
  current_period_start  timestamptz,
  current_period_end    timestamptz,
  next_billing_at       timestamptz,
  auto_renewal          boolean NOT NULL DEFAULT true,
  cancelled_at          timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_subscriptions_user ON subscriptions(user_id);

-- 4. point_ledger
CREATE TABLE IF NOT EXISTS point_ledger (
  id          bigserial PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        text NOT NULL,  -- subscription_grant | purchase | use | expire | refund
  amount      int NOT NULL,   -- 양수=입금, 음수=차감
  balance     int NOT NULL,   -- 거래 후 잔액 (빠른 조회용 비정규화)
  ref_id      text,
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_point_ledger_user ON point_ledger(user_id, created_at DESC);

-- 5. brand_profiles
CREATE TABLE IF NOT EXISTS brand_profiles (
  id               uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name             text NOT NULL,
  industry         text,
  target_customer  text,
  brand_tone       text[],
  primary_color    text,
  secondary_color  text,
  keywords         text[],
  avoid_words      text[],
  products         jsonb,        -- 주요 상품 목록
  extra_context    text,
  logo_url         text,
  is_default       boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_brand_profiles_user ON brand_profiles(user_id);

-- 6. products — 상품 기반 생성용 (Phase 2)
CREATE TABLE IF NOT EXISTS products (
  id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  brand_id    uuid REFERENCES brand_profiles(id) ON DELETE SET NULL,
  name        text NOT NULL,
  price       int,
  category    text,
  features    text[],
  image_url   text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_products_user ON products(user_id);

-- 7. creations — 생성 이력
CREATE TABLE IF NOT EXISTS creations (
  id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  brand_id       uuid REFERENCES brand_profiles(id) ON DELETE SET NULL,
  product_id     uuid REFERENCES products(id) ON DELETE SET NULL,
  creation_type  text NOT NULL,   -- blog | instagram | detail_page | thumbnail_text | ad_copy | ...
  input_data     jsonb,
  output_data    jsonb,
  points_used    int NOT NULL DEFAULT 0,
  status         text NOT NULL DEFAULT 'done',  -- generating | done | failed
  model_used     text,
  generation_ms  int,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_creations_user ON creations(user_id, created_at DESC);

-- 8. payments
CREATE TABLE IF NOT EXISTS payments (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  payment_id      text UNIQUE,
  payment_type    text NOT NULL,  -- subscription | point_purchase
  plan_type       text,
  points_granted  int NOT NULL DEFAULT 0,
  amount          int NOT NULL,
  supply_amount   int,
  tax_amount      int,
  status          text NOT NULL DEFAULT 'paid',  -- paid | failed | cancelled
  refund_status   text,
  refund_amount   int,
  paid_at         timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_payments_user ON payments(user_id, created_at DESC);

-- 9. saas_config — 어드민 시스템 설정 (API 키 등 암호화 저장)
CREATE TABLE IF NOT EXISTS saas_config (
  id           bigserial PRIMARY KEY,
  key          text UNIQUE NOT NULL,
  value_text   text,
  value_secret text,  -- Fernet 암호화
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 10. consent_logs — 약관 동의 이력
CREATE TABLE IF NOT EXISTS consent_logs (
  id             bigserial PRIMARY KEY,
  user_id        uuid REFERENCES users(id) ON DELETE SET NULL,
  email          text,
  consent_type   text NOT NULL,  -- terms | privacy
  terms_version  text,
  agreed_at      timestamptz NOT NULL DEFAULT now(),
  ip_address     text,
  user_agent     text
);

-- 11. RLS 비활성화 (서비스 키 사용, 앱 레벨에서 user_id 필터링)
ALTER TABLE users             DISABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions     DISABLE ROW LEVEL SECURITY;
ALTER TABLE point_ledger      DISABLE ROW LEVEL SECURITY;
ALTER TABLE brand_profiles    DISABLE ROW LEVEL SECURITY;
ALTER TABLE products          DISABLE ROW LEVEL SECURITY;
ALTER TABLE creations         DISABLE ROW LEVEL SECURITY;
ALTER TABLE payments          DISABLE ROW LEVEL SECURITY;
ALTER TABLE saas_config       DISABLE ROW LEVEL SECURITY;
ALTER TABLE consent_logs      DISABLE ROW LEVEL SECURITY;
```

---

## 4. Flask 블루프린트 구조

```
app.py                        # 팩토리: create_app()
config.py                     # Dev / Prod 설정
models.py                     # User, PLAN_FEATURES, POINT_COSTS
auth.py                       # /login /register /logout /find-account /reset-password

blueprints/
  landing.py                  # GET /
  main.py                     # /dashboard /onboarding /history /settings
  brand.py                    # /brand (CRUD 브랜드 프로필)
  billing.py                  # /billing /billing/points
  create/
    __init__.py               # create_bp
    _base.py                  # run_text_generation() 공통 헬퍼
    blog.py                   # /create/blog
    instagram.py              # /create/instagram
    detail_page.py            # /create/detail-page
    thumbnail.py              # /create/thumbnail
    ad_copy.py                # /create/ad-copy
    brand_kit.py              # /create/brand-kit (Growth+)
    hub.py                    # /create/hub (이미지 허브)
  admin/
    __init__.py               # admin_bp, @require_superadmin
    dashboard_views.py        # /admin/
    users_views.py            # /admin/users
    settings_views.py         # /admin/settings (saas_config 관리)

services/
  config_service.py           # get_config(key): env → DB 폴백
  claude_service.py           # generate_text(), build_brand_context()
  imagen_service.py           # generate_image(), generate_card_news()
  point_service.py            # get_balance(), use_points(), add_points()
  payment_service.py          # PortOne v2 연동
  email.py                    # SMTP 이메일 발송
  crypto.py                   # Fernet 암호화/복호화
  rate_limiter.py             # IP 레이트 리밋 (Redis/메모리)
  validators.py               # 이메일·비밀번호 검증
  tz_utils.py                 # KST 시간 처리
  prompts/
    blog.py                   # build_prompt(brand, input) → (system, user)
    instagram.py
    detail_page.py
    thumbnail.py
```

---

## 5. 포인트 비용표

| 타입 | 설명 | 비용 |
|------|------|------|
| `blog` | 블로그 포스트 (SEO) | 80P |
| `instagram` | 인스타 캡션 + 해시태그 | 30P |
| `detail_page` | 상세페이지 카피 풀세트 | 150P |
| `thumbnail_text` | 썸네일 문구 5종 | 40P |
| `ad_copy` | 광고 카피 세트 | 60P |
| `press_release` | 보도자료 | 200P |
| `brand_package` | 브랜드 정체성 패키지 | 1500P |
| `product_launch` | 상품 런칭 패키지 | 2000P |
| `img_preview` | FLUX Schnell — 빠른 시안 | 50P |
| `img_standard` | FLUX Pro — 브랜드 에셋 | 300P |
| `img_hq` | FLUX Pro Ultra — 최고화질 | 600P |
| `img_ideogram` | Ideogram 3.0 — 한글 타이포 | 400P |
| `img_card_news` | FLUX + PIL 합성 카드뉴스 | 800P |
| `logo` | Ideogram 로고 시안 | 800P |

---

## 6. 요금제

| 플랜 | 월 가격 | 월 포인트 | 브랜드 프로필 | 이미지 생성 | 브랜드 키트 |
|------|---------|-----------|--------------|------------|------------|
| Free | 0원 | 0P | 1개 | ✗ | ✗ |
| Starter | 9,900원 | 3,000P | 1개 | ✗ | ✗ |
| Growth | 24,900원 | 10,000P | 3개 | ✓ | ✓ |
| Pro | 49,900원 | 25,000P | 5개 | ✓ | ✓ |

**포인트 충전:** 1,000P 9,900원 / 3,000P 24,900원 / 10,000P 69,900원

---

## 7. AI 모델 믹스

| 용도 | 모델 | 비고 |
|------|------|------|
| 텍스트 전체 | Claude claude-sonnet-4-6 | 메인 엔진 |
| 이미지 프리뷰 | FLUX.2 Schnell (fal-ai/flux/schnell) | 50P |
| 이미지 표준 | FLUX.2 Pro (fal-ai/flux-pro) | 300P |
| 이미지 최고화질 | FLUX.2 Pro Ultra (fal-ai/flux-pro/v1.1-ultra) | 600P |
| 한글 타이포/로고 | Ideogram 3.0 (V_3, DESIGN) | 400~800P |
| 카드뉴스 합성 | FLUX 배경 + Python PIL 오버레이 | 800P |

---

## 8. 환경변수 구성

### Render 환경변수 (필수)
```
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
SUPABASE_ANON_KEY=eyJ...
SECRET_KEY=<랜덤 64자>
ENCRYPTION_KEY=<랜덤 64자>
FLASK_ENV=production
APP_URL=https://maesil-studio.onrender.com
```

### 어드민 시스템 설정 (saas_config DB — /admin/settings)
```
anthropic_api_key   ← Claude API
fal_api_key         ← FLUX 이미지
ideogram_api_key    ← Ideogram
portone_api_secret
portone_store_id
portone_channel_card
portone_channel_kakao
image_provider      ← flux | ideogram
smtp_host / smtp_port / smtp_user / smtp_password / smtp_from
```

---

## 9. API 엔드포인트 맵

```
GET  /                        랜딩 페이지
GET  /login                   로그인
POST /login
GET  /register                회원가입
POST /register
GET  /logout
POST /find-account            비밀번호 찾기
GET  /reset-password
POST /reset-password

GET  /dashboard               대시보드
GET  /onboarding              브랜드 온보딩
POST /onboarding
GET  /history                 생성 이력
GET  /history/<id>
GET  /settings                내 설정
POST /settings
POST /settings/change-password

GET  /brand/                  브랜드 목록
GET  /brand/new               새 브랜드
POST /brand/new
GET  /brand/<id>/edit
POST /brand/<id>/edit
POST /brand/<id>/delete
POST /brand/<id>/set-default

GET  /billing/                구독 관리
GET  /billing/points          포인트 충전
POST /billing/webhook         PortOne 웹훅

GET  /create/blog             블로그 생성 페이지
POST /create/blog/generate    블로그 생성 API (JSON)
GET  /create/instagram
POST /create/instagram/generate
GET  /create/detail-page
POST /create/detail-page/generate
GET  /create/thumbnail
POST /create/thumbnail/generate
GET  /create/ad-copy
POST /create/ad-copy/generate
GET  /create/brand-kit        (Growth+)
POST /create/brand-kit/generate
GET  /create/hub              이미지 생성 허브 (Growth+)
POST /create/image/generate   이미지 생성 API (JSON)

GET  /admin/                  관리자 대시보드
GET  /admin/users             회원 목록
GET  /admin/users/<id>
POST /admin/users/<id>/toggle-active
GET  /admin/settings          시스템 설정
POST /admin/settings
```

---

## 10. 개발 로드맵

### Phase 1 — 현재 구현 완료 ✅
- [x] 인증 (로그인/가입/비밀번호 재설정)
- [x] 브랜드 프로필 CRUD
- [x] 포인트 시스템 (차감/충전/이력)
- [x] Claude 텍스트 생성 (블로그/인스타/상세페이지/썸네일/광고카피)
- [x] 어드민 대시보드 + 시스템 설정
- [x] config_service (환경변수 → DB 폴백)
- [x] 빨강-핑크 브랜드 컬러 (#e8355a)

### Phase 2 — 이미지 생성
- [ ] FLUX + Ideogram 이미지 생성 UI 완성
- [ ] PIL 카드뉴스 합성
- [ ] Supabase Storage 이미지 보관
- [ ] 상품(products) 테이블 + 상품 기반 생성 플로우

### Phase 3 — 전환 + 확산
- [ ] 랜딩페이지 구조 생성기
- [ ] 인플루언서 DM 메시지 + 협찬 제안서 생성
- [ ] 채널별 배포 캘린더 생성
- [ ] 매실 인사이트 판매 데이터 연동

### Phase 4 — 성과 분석
- [ ] CTR / ROAS 예측 모델
- [ ] 콘텐츠 성과 피드백 루프
- [ ] A/B 테스트 세트 구성 지원

---

## 11. Storage 버킷 (Supabase)

```
버킷명: creations
Public: ON
파일 크기 제한: 10MB
경로 구조: {user_id}/{uuid}_{filename}
```

---

## 12. 보안

- bcrypt 비밀번호 해싱 (rounds=12)
- 로그인 실패 5회 시 계정 15분 잠금
- IP 레이트 리밋: 15분 20회
- CSRF 토큰 (Flask-WTF)
- Fernet 대칭 암호화 (DB 저장 API 키)
- HTTPS only (Render + SESSION_COOKIE_SECURE)
- X-Frame-Options, X-XSS-Protection 헤더
- 세션 비활성 120분 자동 만료
