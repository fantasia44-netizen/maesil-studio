-- 009_user_last_seen.sql
-- 유저 마지막 접속 시간 추적용 컬럼 + 인덱스

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_users_last_seen
  ON users (last_seen_at DESC NULLS LAST);
