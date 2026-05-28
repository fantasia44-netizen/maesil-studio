-- 011_billing_upgrade.sql
-- 매실인사이트 빌링 시스템 수준으로 업그레이드
-- : 빌링키 저장, VAT 분리, 환불 추적, 던닝(실패 재시도), 웹훅 시크릿

-- ──────────────────────────────────────────
-- 1. payments — 누락 컬럼 추가
-- ──────────────────────────────────────────
ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS method           TEXT,          -- 결제수단 표시명 (예: "VISA *4242")
    ADD COLUMN IF NOT EXISTS pg_provider      TEXT,          -- PG사 (card / kakaopay)
    ADD COLUMN IF NOT EXISTS receipt_url      TEXT,          -- 포트원 영수증 URL
    ADD COLUMN IF NOT EXISTS raw_data         JSONB,         -- PortOne 원본 응답 (감사용)
    ADD COLUMN IF NOT EXISTS refund_reason    TEXT,          -- 환불 사유
    ADD COLUMN IF NOT EXISTS refund_payment_id TEXT,         -- PortOne 취소 ID
    ADD COLUMN IF NOT EXISTS refund_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS refunded_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMPTZ;

-- supply_amount / tax_amount 이미 있음(003~) — 없을 경우 대비
ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS supply_amount    INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tax_amount       INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS refund_amount    INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS refund_status    TEXT;          -- null / processing / completed / requested

-- payment_id UNIQUE (웹훅 멱등성)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'payments_payment_id_key'
    ) THEN
        ALTER TABLE payments ADD CONSTRAINT payments_payment_id_key UNIQUE (payment_id);
    END IF;
END $$;

-- ──────────────────────────────────────────
-- 2. subscriptions — 던닝 + 빌링키 참조
-- ──────────────────────────────────────────
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS failed_attempt_count INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_retry_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS billing_key          TEXT;  -- 갱신용 빌링키 (operators/users 에서 복사)

-- ──────────────────────────────────────────
-- 3. users — 개인 B2C 빌링키
-- ──────────────────────────────────────────
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS billing_key    TEXT,
    ADD COLUMN IF NOT EXISTS billing_key_pg TEXT,          -- card / kakaopay
    ADD COLUMN IF NOT EXISTS billing_key_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS card_info      JSONB;         -- {pg, brand, last4, expiry}

-- ──────────────────────────────────────────
-- 4. operators — 팀 빌링키
-- ──────────────────────────────────────────
ALTER TABLE operators
    ADD COLUMN IF NOT EXISTS billing_key    TEXT,
    ADD COLUMN IF NOT EXISTS billing_key_pg TEXT,
    ADD COLUMN IF NOT EXISTS billing_key_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS card_info      JSONB;

-- ──────────────────────────────────────────
-- 5. saas_config — 웹훅 시크릿 키 메모
-- (실제 값은 어드민 → 시스템 설정 UI에서 입력)
-- ──────────────────────────────────────────
INSERT INTO saas_config (key, value_text, created_at, updated_at)
VALUES (
    'portone_webhook_secret',
    '',
    NOW(), NOW()
)
ON CONFLICT (key) DO NOTHING;

-- ──────────────────────────────────────────
-- 6. 인덱스
-- ──────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_payments_refund_status
    ON payments (refund_status) WHERE refund_status IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_subscriptions_renewal
    ON subscriptions (next_billing_at, auto_renewal, status)
    WHERE auto_renewal = TRUE;
