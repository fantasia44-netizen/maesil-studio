-- ═══════════════════════════════════════════════════════════
-- 005_insight_operator_pool.sql
-- maesil_insight_connections 에 operator_id 추가
--
-- 배경:
--   maesil_insight_connections 는 user_id 단위로 저장돼 있어서
--   operator admin 이 토큰을 등록해도 팀원들은 해당 연결을 못 봄.
--   → /integrations 에서 "미연결" 표시 + 가져오기 불가.
--
-- 해결:
--   operator_id 컬럼 추가 + UNIQUE 제약 (operator 당 1개 허용).
--   저장 시 operator_id 가 있으면 operator 단위로 upsert.
--   조회 시 operator 소속 사용자는 operator 연결 우선 조회.
--
-- 멱등: IF NOT EXISTS / ADD CONSTRAINT IF NOT EXISTS
-- ═══════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE maesil_insight_connections
  ADD COLUMN IF NOT EXISTS operator_id UUID REFERENCES operators(id) ON DELETE CASCADE;

-- operator 당 1개 연결만 허용 (NULL 은 개인 사용자 — 중복 가능)
CREATE UNIQUE INDEX IF NOT EXISTS uq_insight_conn_operator
  ON maesil_insight_connections(operator_id)
  WHERE operator_id IS NOT NULL;

COMMENT ON COLUMN maesil_insight_connections.operator_id IS
    '팀 연결 풀 키. 채워지면 같은 operator 팀원 전체가 공유.';

COMMIT;

NOTIFY pgrst, 'reload schema';
