-- ═══════════════════════════════════════════════════════════
-- 004_brand_operator_pool.sql
-- brand_profiles + products 에 operator_id 컬럼 추가 + 기존 데이터 백필
--
-- 배경:
--   003_team_pool.sql 이 point_ledger/subscriptions/payments/creations 에만
--   operator_id 를 추가했으나 brand_profiles, products 는 누락됐다.
--
--   결과:
--     · get_accessible_brands() 에서 .eq('operator_id', ...) 실행 시
--       "column does not exist" 에러 → brand_count = 0 → 대시보드 "브랜드 생성하기"
--     · product.py 의 .or_('operator_id.eq.X,...') 도 동일한 에러
--
-- 정책:
--   - operator_id 가 채워진 행: 팀 공유 (팀원 전체 접근).
--   - operator_id 가 NULL 인 행: 개인 사용자 소유 (종전과 동일).
--   - 백필: users.operator_id 가 있는 사용자의 기존 행들에 operator_id 채움.
--
-- 멱등: IF NOT EXISTS + WHERE operator_id IS NULL 조건
-- ═══════════════════════════════════════════════════════════

BEGIN;

-- ─────────────────────────────────────────────────────────────
-- brand_profiles
-- ─────────────────────────────────────────────────────────────
ALTER TABLE brand_profiles
  ADD COLUMN IF NOT EXISTS operator_id UUID REFERENCES operators(id) ON DELETE SET NULL;

COMMENT ON COLUMN brand_profiles.operator_id IS
    '팀 브랜드 풀 키. 채워지면 같은 operator 팀원 전체가 조회/사용 가능.';

CREATE INDEX IF NOT EXISTS idx_brand_profiles_operator
  ON brand_profiles(operator_id) WHERE operator_id IS NOT NULL;

-- Backfill: operator 소속 사용자의 기존 브랜드에 operator_id 채움
UPDATE brand_profiles bp
   SET operator_id = u.operator_id
  FROM users u
 WHERE bp.user_id = u.id
   AND u.operator_id IS NOT NULL
   AND bp.operator_id IS NULL;

-- ─────────────────────────────────────────────────────────────
-- products
-- ─────────────────────────────────────────────────────────────
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS operator_id UUID REFERENCES operators(id) ON DELETE SET NULL;

COMMENT ON COLUMN products.operator_id IS
    '팀 상품 풀 키. operator 소속 사용자는 팀 단위 상품을 공유.';

CREATE INDEX IF NOT EXISTS idx_products_operator
  ON products(operator_id) WHERE operator_id IS NOT NULL;

-- Backfill: operator 소속 사용자의 기존 상품에 operator_id 채움
UPDATE products p
   SET operator_id = u.operator_id
  FROM users u
 WHERE p.user_id = u.id
   AND u.operator_id IS NOT NULL
   AND p.operator_id IS NULL;

COMMIT;

NOTIFY pgrst, 'reload schema';
