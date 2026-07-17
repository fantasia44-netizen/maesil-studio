-- ═══════════════════════════════════════════════════════════
-- 009_wordpress_connections_brand_fix.sql
-- 008에서 만든 브랜드 유니크 "부분 인덱스"를 진짜 UNIQUE 제약으로 교체.
--
-- 문제: partial unique index(WHERE brand_id IS NOT NULL)는 PostgREST의
--   upsert(on_conflict='brand_id')가 충돌 대상(arbiter)으로 인식하지 못해
--   "there is no unique or exclusion constraint matching the ON CONFLICT
--   specification" (42P10) 오류가 발생함.
-- 해결: 일반 UNIQUE 제약으로 교체 — Postgres는 NULL 값끼리는 서로 다르다고
--   보므로, brand_id가 NULL인 기존 팀/개인 연결 행이 여러 개 있어도
--   전혀 문제 없음(유니크 체크는 NOT NULL 값끼리만 적용됨).
-- ═══════════════════════════════════════════════════════════

BEGIN;

DROP INDEX IF EXISTS uq_wp_conn_brand;

ALTER TABLE wordpress_connections
  ADD CONSTRAINT uq_wp_conn_brand_id UNIQUE (brand_id);

COMMIT;

NOTIFY pgrst, 'reload schema';
