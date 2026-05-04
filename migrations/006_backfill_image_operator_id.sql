-- ═══════════════════════════════════════════════════════════
-- 006_backfill_image_operator_id.sql
-- image.py / instagram.py / logo.py / shorts.py 가 operator_id 없이
-- 저장한 creations 행을 소급 보정.
--
-- 배경:
--   migration 003 backfill 이후에 생성된 이미지/로고/쇼츠 creation 행들이
--   operator_id = NULL 로 저장돼서 팀 히스토리에 안 보이는 문제.
--   (text 생성은 _base.py 가 operator_id 를 올바르게 기록하고 있었음)
--
-- 해결:
--   user_id 로 users 테이블 조인 → operator_id 소급 채우기
--   조건: operator_id IS NULL AND users.operator_id IS NOT NULL
--
-- 멱등: WHERE operator_id IS NULL 이라 재실행 무해
-- ═══════════════════════════════════════════════════════════

BEGIN;

UPDATE creations
   SET operator_id = u.operator_id
  FROM users u
 WHERE creations.user_id   = u.id
   AND creations.operator_id IS NULL
   AND u.operator_id IS NOT NULL;

COMMIT;

NOTIFY pgrst, 'reload schema';
