-- ═══════════════════════════════════════════════════════════
-- 001_external_integrations.sql
-- 매실 인사이트 외부 API 연동 — 토큰 보관 + 가져온 상품 출처 추적
--
-- 적용 방식: schema.sql 과 동일하게 Supabase SQL Editor 또는 직접 psql.
-- 모든 DDL 은 IF NOT EXISTS — 멱등성 보장.
--
-- 배경: doc/maesil_insight_external_api_request_v1.md §2 사용자 흐름
--   사용자가 인사이트에서 발급받은 토큰을 스튜디오에 입력 →
--   스튜디오가 그 토큰으로 인사이트의 상품 데이터를 조회 →
--   상품 등록 시 source='maesil_insight', source_ref=seller_product_id 로 저장.
-- ═══════════════════════════════════════════════════════════

BEGIN;

-- ── products: 가져오기 출처 추적 ────────────────────────────
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS source     TEXT NOT NULL DEFAULT 'manual',
  ADD COLUMN IF NOT EXISTS source_ref TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS image_url  TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_products_source
  ON products(user_id, source) WHERE source <> 'manual';

-- 같은 인사이트 상품을 두 번 가져오지 못하도록 (사용자 단위 unique)
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_source_ref
  ON products(user_id, source, source_ref)
  WHERE source <> 'manual' AND source_ref <> '';

COMMENT ON COLUMN products.source IS
    'manual | maesil_insight | (향후 외부 가져오기 채널)';
COMMENT ON COLUMN products.source_ref IS
    '외부 출처의 식별자 (인사이트의 경우 seller_product_id).';

-- ── maesil_insight_connections: 사용자별 토큰 + /me 캐시 ───
CREATE TABLE IF NOT EXISTS maesil_insight_connections (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  -- Fernet 암호화 (services.crypto) — 평문이 매 호출마다 필요하므로 해시 X
  token_encrypted        TEXT NOT NULL,
  token_prefix           TEXT NOT NULL,           -- 'mi_xxxxxxxx' 마스킹 표시용
  -- /me 응답 캐시 (연결 시 채움, 주기적으로 갱신 가능)
  insight_operator_id    UUID,
  insight_operator_name  TEXT,
  insight_plan           TEXT,
  scopes                 TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  expires_at             TIMESTAMPTZ,
  -- 메타
  connected_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at       TIMESTAMPTZ,
  last_used_at           TIMESTAMPTZ,
  last_error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_insight_conn_user
  ON maesil_insight_connections(user_id);

ALTER TABLE maesil_insight_connections DISABLE ROW LEVEL SECURITY;

COMMENT ON TABLE maesil_insight_connections IS
    '매실 인사이트 외부 API 토큰. 사용자당 1개. 평문은 Fernet 암호화 저장.';

COMMIT;
