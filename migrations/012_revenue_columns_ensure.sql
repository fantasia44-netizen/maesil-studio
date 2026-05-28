-- 012_revenue_columns_ensure.sql
-- revenue_views.py 가 의존하는 payments 컬럼 일괄 보장 (멱등)
-- 003_team_pool / 011_billing_upgrade 가 미적용인 DB 에도 안전하게 실행 가능.

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS operator_id        UUID REFERENCES operators(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS supply_amount      INT  DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tax_amount         INT  DEFAULT 0,
    ADD COLUMN IF NOT EXISTS refund_amount      INT  DEFAULT 0,
    ADD COLUMN IF NOT EXISTS refund_status      TEXT,          -- null / processing / requested / completed
    ADD COLUMN IF NOT EXISTS refund_reason      TEXT,
    ADD COLUMN IF NOT EXISTS refund_payment_id  TEXT,
    ADD COLUMN IF NOT EXISTS refund_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS refunded_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pg_provider        TEXT,
    ADD COLUMN IF NOT EXISTS method             TEXT,
    ADD COLUMN IF NOT EXISTS receipt_url        TEXT,
    ADD COLUMN IF NOT EXISTS raw_data           JSONB,
    ADD COLUMN IF NOT EXISTS order_name         TEXT,
    ADD COLUMN IF NOT EXISTS updated_at         TIMESTAMPTZ;

-- payment_id UNIQUE 제약 (웹훅 멱등성)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'payments_payment_id_key'
          AND conrelid = 'payments'::regclass
    ) THEN
        ALTER TABLE payments ADD CONSTRAINT payments_payment_id_key UNIQUE (payment_id);
    END IF;
END $$;

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_payments_paid_at
    ON payments (paid_at DESC) WHERE paid_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_refund_status
    ON payments (refund_status) WHERE refund_status IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_operator
    ON payments (operator_id, paid_at DESC) WHERE operator_id IS NOT NULL;

NOTIFY pgrst, 'reload schema';
