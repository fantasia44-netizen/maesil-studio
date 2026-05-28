-- 012_revenue_columns_ensure.sql
-- payments 테이블 생성(없는 경우) + 누락 컬럼 추가 (멱등)
-- FK 없이 생성 — 실제 DB의 users.id / operators.id 타입에 무관하게 동작

CREATE TABLE IF NOT EXISTS payments (
  id              bigserial PRIMARY KEY,
  user_id         bigint NOT NULL,
  payment_id      text UNIQUE,
  payment_type    text NOT NULL DEFAULT 'subscription',
  plan_type       text,
  points_granted  int NOT NULL DEFAULT 0,
  amount          int NOT NULL DEFAULT 0,
  supply_amount   int DEFAULT 0,
  tax_amount      int DEFAULT 0,
  status          text NOT NULL DEFAULT 'paid',
  refund_status   text,
  refund_amount   int DEFAULT 0,
  operator_id     bigint,
  pg_provider     text,
  method          text,
  receipt_url     text,
  raw_data        jsonb,
  order_name      text,
  refund_reason   text,
  refund_payment_id text,
  refund_requested_at timestamptz,
  refunded_at     timestamptz,
  paid_at         timestamptz,
  updated_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE payments ADD COLUMN IF NOT EXISTS operator_id         bigint;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS supply_amount       int DEFAULT 0;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS tax_amount          int DEFAULT 0;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_amount       int DEFAULT 0;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_status       text;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_reason       text;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_payment_id   text;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refund_requested_at timestamptz;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS refunded_at         timestamptz;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS pg_provider         text;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS method              text;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS receipt_url         text;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS raw_data            jsonb;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS order_name          text;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_at          timestamptz;

ALTER TABLE payments DISABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_payments_user ON payments (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments (paid_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_refund_status ON payments (refund_status) WHERE refund_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payments_operator ON payments (operator_id, paid_at DESC) WHERE operator_id IS NOT NULL;
