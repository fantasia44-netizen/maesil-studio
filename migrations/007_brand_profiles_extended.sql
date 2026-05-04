-- 브랜드 프로필 확장 필드 추가
-- 제안서·카탈로그·리플릿 등 고품질 문서 생성에 활용

ALTER TABLE brand_profiles
  ADD COLUMN IF NOT EXISTS founded_year    integer,       -- 창업 연도
  ADD COLUMN IF NOT EXISTS ceo_name        text,          -- 대표자명
  ADD COLUMN IF NOT EXISTS employee_count  text,          -- 직원 규모 (예: "10명", "50~100명")
  ADD COLUMN IF NOT EXISTS address         text,          -- 사업장 주소
  ADD COLUMN IF NOT EXISTS contact_phone   text,          -- 대표 연락처
  ADD COLUMN IF NOT EXISTS contact_email   text,          -- 대표 이메일
  ADD COLUMN IF NOT EXISTS website         text,          -- 홈페이지 URL
  ADD COLUMN IF NOT EXISTS certifications  text,          -- 인증·수상 이력 (자유 텍스트)
  ADD COLUMN IF NOT EXISTS key_stats       text,          -- 핵심 수치 (재구매율, 누적판매 등)
  ADD COLUMN IF NOT EXISTS references_text text;          -- 주요 거래처·납품처
