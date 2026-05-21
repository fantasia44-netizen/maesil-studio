-- 010_point_expiry.sql
-- 포인트 만료 시스템
--   expires_at : 이 항목의 만료 시각 (NULL = 무기한)
--   remaining  : 아직 소비되지 않은 잔여량 (양수 항목에만 의미 있음)
--
-- 동작 원칙:
--   1) add_points() 로 지급한 양수 항목 → remaining = amount 로 초기화
--   2) use_points() 차감 시 → 만료 임박 버킷부터 remaining 감소
--   3) get_balance() 호출 시 → expires_at < NOW() & remaining > 0 항목을
--      'expire' 타입 음수 트랜잭션으로 확정(materialize)한 뒤 잔액 반환
--
-- 기존 데이터 처리:
--   기존 양수 항목은 remaining = amount 로 초기화 (만료 없이 그대로 유지)

ALTER TABLE point_ledger
  ADD COLUMN IF NOT EXISTS expires_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS remaining   INT;

-- 기존 양수 행: remaining 을 amount 와 동일하게 세팅 (이미 일부 사용됐을 수 있으므로
-- 정확한 값을 소급 계산하기 어려움 → 안전하게 amount 로 설정. 어차피 expires_at=NULL 이므로
-- 만료 트리거가 발생하지 않음)
UPDATE point_ledger
SET    remaining = amount
WHERE  amount > 0
  AND  remaining IS NULL;

CREATE INDEX IF NOT EXISTS idx_point_ledger_expires
  ON point_ledger (expires_at)
  WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_point_ledger_remaining
  ON point_ledger (user_id, remaining, expires_at)
  WHERE remaining > 0;
