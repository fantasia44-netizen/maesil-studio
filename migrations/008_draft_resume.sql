-- migrations/008_draft_resume.sql
-- 임시저장(draft) / 작업이력 재개 기능을 위한 creations 테이블 컬럼 추가
-- 실행: Supabase SQL Editor 또는 psql
-- 멱등: IF NOT EXISTS 로 중복 실행 안전

-- 1. 진행 단계 번호 (1=기본설정, 2=소구포인트, 3=초안완료, 4=이미지작업중, 5=완성)
ALTER TABLE creations
  ADD COLUMN IF NOT EXISTS step_reached  INTEGER DEFAULT 1;

-- 2. 각 단계별 JS 상태 직렬화 저장 (angles, selected_angle, blogText, generatedImages 등)
ALTER TABLE creations
  ADD COLUMN IF NOT EXISTS step_data     JSONB DEFAULT '{}'::jsonb;

-- 3. 마지막 수정 시각 (임시저장 목록 최신순 정렬용)
ALTER TABLE creations
  ADD COLUMN IF NOT EXISTS updated_at    TIMESTAMPTZ DEFAULT now();

-- 4. status 컬럼에 'draft' 값 허용 안내 (기존 CHECK 제약이 없다면 별도 작업 불필요)
-- 현재 status 허용값: 'generating' | 'done' | 'failed' | 'draft'
-- CHECK 제약이 있다면 아래 주석 해제 후 실행:
-- ALTER TABLE creations DROP CONSTRAINT IF EXISTS creations_status_check;
-- ALTER TABLE creations ADD CONSTRAINT creations_status_check
--   CHECK (status IN ('generating', 'done', 'failed', 'draft'));

-- 5. 임시저장 목록 조회 성능용 부분 인덱스
CREATE INDEX IF NOT EXISTS idx_creations_draft_user
  ON creations(user_id, creation_type, updated_at DESC)
  WHERE status = 'draft';

CREATE INDEX IF NOT EXISTS idx_creations_draft_operator
  ON creations(operator_id, creation_type, updated_at DESC)
  WHERE status = 'draft' AND operator_id IS NOT NULL;

-- 6. 스키마 리로드 (PostgREST 사용 시)
NOTIFY pgrst, 'reload schema';
