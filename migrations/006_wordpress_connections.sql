-- ═══════════════════════════════════════════════════════════
-- 006_wordpress_connections.sql
-- 워드프레스(자체 호스팅) 연동 — 사이트 주소 + 아이디 + 앱 비밀번호 보관
--
-- 적용 방식: schema.sql 과 동일하게 Supabase SQL Editor 또는 직접 psql.
-- 모든 DDL 은 IF NOT EXISTS — 멱등성 보장.
--
-- 배경: 경험담 '구글(워드프레스)판' 글을 사용자의 워드프레스 사이트에
--   REST API(wp-json/wp/v2/posts)로 바로 초안/발행할 수 있게 한다.
--   인증은 워드프레스 '애플리케이션 비밀번호'(HTTP Basic) 사용.
--   maesil_insight_connections 와 동일한 구조/공유 정책(개인 or 팀 operator).
-- ═══════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS wordpress_connections (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  operator_id            UUID REFERENCES operators(id) ON DELETE CASCADE,
  -- 연결 대상 사이트 (https://example.com, 뒤 슬래시 없음)
  site_url               TEXT NOT NULL,
  wp_username            TEXT NOT NULL,
  -- 애플리케이션 비밀번호: Fernet 암호화 (services.crypto) — 매 호출마다 평문 필요 → 해시 X
  app_password_encrypted TEXT NOT NULL,
  password_prefix        TEXT NOT NULL DEFAULT '',   -- 'abcd****' 마스킹 표시용
  -- /users/me 응답 캐시
  wp_display_name        TEXT,
  wp_user_id             BIGINT,
  -- 퍼머링크(고유주소)가 꺼진 사이트는 ?rest_route= 폴백을 쓰는데, 그 결과를 캐시
  use_rest_route         BOOLEAN NOT NULL DEFAULT false,
  -- 메타
  connected_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at       TIMESTAMPTZ,
  last_used_at           TIMESTAMPTZ,
  last_error             TEXT
);

-- operator 당 1개 연결만 허용 (NULL 은 개인 사용자 — user_id UNIQUE 로 제한)
CREATE UNIQUE INDEX IF NOT EXISTS uq_wp_conn_operator
  ON wordpress_connections(operator_id)
  WHERE operator_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wp_conn_user
  ON wordpress_connections(user_id);

ALTER TABLE wordpress_connections DISABLE ROW LEVEL SECURITY;

COMMENT ON TABLE wordpress_connections IS
    '워드프레스 REST API 연동. 개인은 user_id 당 1개, 팀은 operator_id 당 1개. 앱 비밀번호는 Fernet 암호화 저장.';
COMMENT ON COLUMN wordpress_connections.operator_id IS
    '팀 연결 풀 키. 채워지면 같은 operator 팀원 전체가 공유.';
COMMENT ON COLUMN wordpress_connections.use_rest_route IS
    '퍼머링크 꺼진 사이트용 ?rest_route= 폴백 사용 여부 (연결 시 자동 감지).';

COMMIT;

NOTIFY pgrst, 'reload schema';
