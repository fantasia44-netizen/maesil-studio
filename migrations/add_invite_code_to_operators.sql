-- operators 테이블에 invite_code 컬럼 추가
-- Supabase SQL Editor 또는 psql 에서 실행

ALTER TABLE operators
  ADD COLUMN IF NOT EXISTS invite_code VARCHAR(16) UNIQUE;

-- 기존 operator 레코드에 초기 코드 부여 (선택)
UPDATE operators
SET invite_code = upper(substr(md5(random()::text), 1, 8))
WHERE invite_code IS NULL;
