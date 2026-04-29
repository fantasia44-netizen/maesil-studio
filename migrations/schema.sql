-- 매실 크리에이터 DB 스키마 (Supabase PostgreSQL)
-- Supabase SQL Editor에서 순서대로 실행하세요.

-- ── 확장 ────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── users ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  email               text UNIQUE NOT NULL,
  password_hash       text NOT NULL,
  name                text,
  phone               text,
  plan_type           text NOT NULL DEFAULT 'free',
  is_active           boolean NOT NULL DEFAULT true,
  is_deleted          boolean NOT NULL DEFAULT false,
  site_role           text NOT NULL DEFAULT 'user',   -- 'user' | 'superadmin'
  failed_login_count  int NOT NULL DEFAULT 0,
  locked_until        timestamptz,
  last_login_at       timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_email ON users(email);

-- ── subscriptions ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
  id                    uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_type             text NOT NULL,
  status                text NOT NULL DEFAULT 'trial',  -- trial | active | past_due | cancelled
  current_period_start  timestamptz,
  current_period_end    timestamptz,
  next_billing_at       timestamptz,
  auto_renewal          boolean NOT NULL DEFAULT true,
  cancelled_at          timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_subscriptions_user ON subscriptions(user_id);

-- ── point_ledger ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS point_ledger (
  id          bigserial PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        text NOT NULL,    -- subscription_grant | purchase | use | expire | refund
  amount      int NOT NULL,     -- 양수=입금, 음수=차감
  balance     int NOT NULL,     -- 거래 후 잔액
  ref_id      text,             -- creation_id 또는 payment_id
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_point_ledger_user ON point_ledger(user_id, created_at DESC);

-- ── brand_profiles ───────────────────────────────────────
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
  products         jsonb,
  extra_context    text,
  logo_url         text,
  is_default       boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_brand_profiles_user ON brand_profiles(user_id);

-- ── creations ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS creations (
  id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  brand_id       uuid REFERENCES brand_profiles(id) ON DELETE SET NULL,
  creation_type  text NOT NULL,
  input_data     jsonb,
  output_data    jsonb,
  points_used    int NOT NULL DEFAULT 0,
  status         text NOT NULL DEFAULT 'done',   -- generating | done | failed
  model_used     text,
  generation_ms  int,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_creations_user ON creations(user_id, created_at DESC);

-- ── payments ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  payment_id      text UNIQUE,           -- PortOne payment_id
  payment_type    text NOT NULL,          -- subscription | point_purchase
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

-- ── saas_config (시스템 설정 — 어드민 전용) ────────────
CREATE TABLE IF NOT EXISTS saas_config (
  id           bigserial PRIMARY KEY,
  key          text UNIQUE NOT NULL,
  value_text   text,
  value_secret text,   -- 암호화된 값
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ── consent_logs (약관 동의 이력) ────────────────────────
CREATE TABLE IF NOT EXISTS consent_logs (
  id             bigserial PRIMARY KEY,
  user_id        uuid REFERENCES users(id) ON DELETE SET NULL,
  email          text,
  consent_type   text NOT NULL,   -- terms | privacy
  terms_version  text,
  agreed_at      timestamptz NOT NULL DEFAULT now(),
  ip_address     text,
  user_agent     text
);

-- ── RLS 비활성화 (서비스 키 사용 — 앱에서 직접 필터링) ──
ALTER TABLE users             DISABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions     DISABLE ROW LEVEL SECURITY;
ALTER TABLE point_ledger      DISABLE ROW LEVEL SECURITY;
ALTER TABLE brand_profiles    DISABLE ROW LEVEL SECURITY;
ALTER TABLE creations         DISABLE ROW LEVEL SECURITY;
ALTER TABLE payments          DISABLE ROW LEVEL SECURITY;
ALTER TABLE saas_config       DISABLE ROW LEVEL SECURITY;
ALTER TABLE consent_logs      DISABLE ROW LEVEL SECURITY;

-- ── Storage 버킷 (Supabase Dashboard에서 수동 생성) ─────
-- 버킷 이름: creations
-- Public: true
-- 파일 크기 제한: 10MB
