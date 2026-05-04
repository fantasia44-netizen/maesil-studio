-- ═══════════════════════════════════════════════════════════
-- 003_team_pool.sql
-- 팀(Operator) 풀 — 포인트/구독/결제/생성물 공유
--
-- 배경:
--   초대 코드로 가입한 팀 멤버는 본인 user_id 로만 포인트/구독이 묶여 있어
--   "팀 관리자가 결제했는데 멤버는 콘텐츠 못 만들고, 자기 생성물만 보이는"
--   상태였음. 이 마이그레이션은 4개 핵심 테이블에 operator_id 컬럼을 추가해
--   operator 단위로 풀을 통합한다.
--
-- 정책:
--   - operator_id 가 채워진 행은 "operator 풀" 행위. 팀 전원이 공유.
--   - operator_id 가 NULL 인 행은 개인 사용자(B2C) 행위 — 종전과 동일.
--   - user_id 는 모든 행에 그대로 유지(누가 사용/생성했는지 감사 추적).
--
-- 멱등 (모든 DDL IF NOT EXISTS / WHERE NOT EXISTS).
-- ═══════════════════════════════════════════════════════════

BEGIN;

-- ── 컬럼 추가 ──────────────────────────────────────────────
ALTER TABLE point_ledger
  ADD COLUMN IF NOT EXISTS operator_id UUID REFERENCES operators(id) ON DELETE CASCADE;

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS operator_id UUID REFERENCES operators(id) ON DELETE CASCADE;

ALTER TABLE payments
  ADD COLUMN IF NOT EXISTS operator_id UUID REFERENCES operators(id) ON DELETE CASCADE;

ALTER TABLE creations
  ADD COLUMN IF NOT EXISTS operator_id UUID REFERENCES operators(id) ON DELETE CASCADE;

COMMENT ON COLUMN point_ledger.operator_id IS
    '팀 풀 키. 채워진 행은 operator 잔액에 반영. 개인 계정은 NULL (user_id 풀).';
COMMENT ON COLUMN subscriptions.operator_id IS
    '팀 구독 풀 키. operator_admin 만 발행/해제. 팀 전원이 권한 공유.';
COMMENT ON COLUMN payments.operator_id IS
    '결제 풀 키. operator 모드면 결제자 user_id + 풀 operator_id 양쪽 기록.';
COMMENT ON COLUMN creations.operator_id IS
    '팀 생성물 풀 키. 같은 operator 멤버는 서로의 생성 이력을 공유 조회.';


-- ── 인덱스 ─────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_point_ledger_operator
  ON point_ledger(operator_id, created_at DESC) WHERE operator_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_subscriptions_operator
  ON subscriptions(operator_id) WHERE operator_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_operator
  ON payments(operator_id, created_at DESC) WHERE operator_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_creations_operator
  ON creations(operator_id, created_at DESC) WHERE operator_id IS NOT NULL;


-- ── Backfill: 기존 데이터에 operator_id 채우기 ─────────────
-- users.operator_id 가 있는 사용자의 과거 행들을 그 operator 로 묶음.
UPDATE point_ledger pl
   SET operator_id = u.operator_id
  FROM users u
 WHERE pl.user_id = u.id
   AND u.operator_id IS NOT NULL
   AND pl.operator_id IS NULL;

UPDATE subscriptions s
   SET operator_id = u.operator_id
  FROM users u
 WHERE s.user_id = u.id
   AND u.operator_id IS NOT NULL
   AND s.operator_id IS NULL;

UPDATE payments p
   SET operator_id = u.operator_id
  FROM users u
 WHERE p.user_id = u.id
   AND u.operator_id IS NOT NULL
   AND p.operator_id IS NULL;

UPDATE creations c
   SET operator_id = u.operator_id
  FROM users u
 WHERE c.user_id = u.id
   AND u.operator_id IS NOT NULL
   AND c.operator_id IS NULL;


-- ── 팀 초대 멤버 잔여 trial 정리 ───────────────────────────
-- 초대로 가입한 팀 멤버에게 잘못 생성된 개인 trial subscription 은
-- 비활성화 (cancelled). operator 단위 구독으로 통합되도록.
UPDATE subscriptions s
   SET status = 'cancelled',
       cancelled_at = COALESCE(s.cancelled_at, now()),
       updated_at = now()
  FROM users u
 WHERE s.user_id = u.id
   AND u.operator_id IS NOT NULL
   AND u.site_role = 'user'           -- operator_admin 은 그대로 둠 (그게 팀 구독)
   AND s.status = 'trial'
   AND s.operator_id IS NULL;


COMMIT;

NOTIFY pgrst, 'reload schema';
