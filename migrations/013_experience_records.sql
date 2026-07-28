-- 013: 경험 데이터 저장소 (E-E-A-T 근거 주입용)
--
-- 운영자의 실제 사업 경험(문제/조치/결과/수치)을 구조화해 저장하고,
-- 마케팅 블로그 생성 시 주제와 매칭되는 경험을 근거로 주입한다.
-- AI가 없는 수치·사례를 지어내지 못하게 하는 핵심 데이터 소스.
-- 실행 위치: 매실스튜디오 Supabase (public 스키마)

CREATE TABLE IF NOT EXISTS experience_records (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  brand_id            uuid,                 -- NULL = 브랜드 공통 경험
  user_id             uuid,
  category            text,                 -- 광고/네이버/쿠팡/제조/물류/3PL/브랜드/ERP/AI/자금/기타
  title               text NOT NULL,
  summary             text,
  problem             text,                 -- 당시 문제
  action              text,                 -- 실제 조치
  result              text,                 -- 조치 후 결과
  numbers_json        jsonb DEFAULT '{}'::jsonb,   -- {before_ad_cost:.., after_ad_cost:.., roas:..}
  platform            text,                 -- 쿠팡/네이버/자사몰 등
  product             text,
  keywords            text[] DEFAULT '{}',  -- 매칭용 키워드
  confidentiality     text DEFAULT 'anonymized',   -- public | anonymized | private
  usable_for_content  boolean DEFAULT true,
  evidence_type       text,                 -- 직접경험/관리자캡처/정산데이터/광고데이터/고객사례/공개자료
  evidence_url        text,
  created_at          timestamptz DEFAULT now(),
  updated_at          timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS experience_records_brand_idx
  ON experience_records (brand_id, usable_for_content);
CREATE INDEX IF NOT EXISTS experience_records_user_idx
  ON experience_records (user_id);
-- 키워드/텍스트 매칭 가속(간단 GIN)
CREATE INDEX IF NOT EXISTS experience_records_keywords_idx
  ON experience_records USING gin (keywords);
