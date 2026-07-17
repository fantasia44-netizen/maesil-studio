-- ═══════════════════════════════════════════════════════════
-- 008_wordpress_connections_brand.sql
-- 워드프레스 연동 — 브랜드별 연결 지원
--
-- 적용 방식: schema.sql 과 동일하게 Supabase SQL Editor 또는 직접 psql.
-- 모든 DDL 은 IF NOT EXISTS — 멱등성 보장.
--
-- 배경: 브랜드를 여러 개 운영하는 팀이 브랜드마다 서로 다른 워드프레스 사이트에
--   발행할 수 있게 한다(예: 매실 → blog.maesil.net, 배마마 → blog.baemama.co.kr).
--   기존 user_id/operator_id 단위 연결(경험담 블로그가 사용, brand_id IS NULL)은
--   그대로 유지하고 건드리지 않는다 — 브랜드 연결은 완전히 별도의 새 행으로,
--   폴백 없이 브랜드 단위로만 조회된다.
-- ═══════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE wordpress_connections
  ADD COLUMN IF NOT EXISTS brand_id UUID REFERENCES brand_profiles(id) ON DELETE CASCADE;

-- 브랜드당 연결 1개만 허용 (NULL 은 기존 user_id/operator_id 단위 연결 — 영향 없음)
CREATE UNIQUE INDEX IF NOT EXISTS uq_wp_conn_brand
  ON wordpress_connections(brand_id)
  WHERE brand_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wp_conn_brand ON wordpress_connections(brand_id);

COMMENT ON COLUMN wordpress_connections.brand_id IS
    '브랜드 전용 연결. NULL이면 기존 팀/개인 공통 연결(경험담 블로그가 계속 사용). '
    '값이 있으면 그 브랜드에서만 쓰이고, 폴백 없이 브랜드 단위로만 조회됨.';

COMMIT;

NOTIFY pgrst, 'reload schema';
