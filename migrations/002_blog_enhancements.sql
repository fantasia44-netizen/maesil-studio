-- ═══════════════════════════════════════════════════════════
-- 002_blog_enhancements.sql
-- 블로그 콘텐츠 품질 개선을 위한 스키마 보강
--
-- 적용: Supabase SQL Editor 또는 psql 에서 실행. 모든 DDL 멱등.
--
-- 추가 영역:
--   1) products.avoid_words      — 상품별 금지어 (3-tier 금지어 중 product 레이어)
--   2) creations 4축 입력 트래킹 — angle / topic / keyword / details / length
--   3) creations 이력 관계      — relation_mode / relation_ref_id (시리즈/변형 추적)
--   4) creations 상품 연결      — product_id (DESIGN.md §3 기준 누락분 보강)
--   5) saas_config 시드        — 카테고리별 법적 금지어 + AI 디스클레이머 기본값
-- ═══════════════════════════════════════════════════════════

BEGIN;

-- ── 1. products: 상품별 금지어 ────────────────────────────────
ALTER TABLE products
  ADD COLUMN IF NOT EXISTS avoid_words TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];

COMMENT ON COLUMN products.avoid_words IS
    '상품별 금지 표현 (3-tier 중 product 레이어). brand.avoid_words 와 시스템 금지어와 합집합으로 프롬프트 주입.';


-- ── 2~4. creations 보강 ─────────────────────────────────────────
ALTER TABLE creations
  -- 상품 연결 (DESIGN.md §3 기준 누락분 — 멱등 보강)
  ADD COLUMN IF NOT EXISTS product_id      UUID REFERENCES products(id) ON DELETE SET NULL,
  -- 4축 입력 (분석/필터링용 — 원본은 input_data jsonb 에도 보존)
  ADD COLUMN IF NOT EXISTS topic           TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS keyword         TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS angle           TEXT NOT NULL DEFAULT '',  -- 정보형|후기형|시기별|비교형|qna|트렌드 등
  ADD COLUMN IF NOT EXISTS length_chars    INT  NOT NULL DEFAULT 0,
  -- 이력 관계 (시리즈/변형/무관)
  ADD COLUMN IF NOT EXISTS relation_mode   TEXT NOT NULL DEFAULT 'new',  -- new|series|variant|ignore
  ADD COLUMN IF NOT EXISTS relation_ref_id UUID REFERENCES creations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_creations_product
  ON creations(product_id) WHERE product_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_creations_user_brand_type_recent
  ON creations(user_id, brand_id, creation_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_creations_relation_ref
  ON creations(relation_ref_id) WHERE relation_ref_id IS NOT NULL;

COMMENT ON COLUMN creations.angle IS
    '블로그 앵글: information|review|timeline|comparison|qna|trend (확장 가능).';
COMMENT ON COLUMN creations.relation_mode IS
    'new: 새 주제 / series: 후속편 / variant: 변형(같은 주제 다른 각도) / ignore: 이력 무시.';
COMMENT ON COLUMN creations.relation_ref_id IS
    'series 또는 variant 일 때 참조한 이전 creation id.';


-- ── 5. saas_config 시드 ────────────────────────────────────────
-- 어드민이 /admin/settings 에서 자유롭게 수정 가능. 여기 값은 초기 기본값.
-- 형식: JSON 문자열로 저장 (앱에서 json.loads). 빈 키만 시드 → 운영 중 수정 보존.

INSERT INTO saas_config (key, value_text)
VALUES
  -- ── 카테고리별 법적 금지/주의 표현 ──
  ('regulatory_keywords_general',
   '["100%","무조건","절대","최고","최상","유일","완벽","독보적","전세계","세계1위","공식인증(허위)"]'),

  ('regulatory_keywords_food',
   '["효능","효과","치료","치유","예방","개선","완화","회복","진정",
     "면역력","면역","항산화","항암","해독","디톡스","항노화","노화방지",
     "다이어트 효과","변비 개선","장 건강","피부 미용","주름 개선",
     "혈관","혈압","혈당","당뇨","고혈압","콜레스테롤"]'),

  ('regulatory_keywords_baby_food',
   '["분유 대용","모유 같은","모유 대신","두뇌발달","IQ","지능 향상",
     "성장발달 보장","면역 강화","뇌 발달","집중력 향상",
     "약","의약품","치료식"]'),

  ('regulatory_keywords_health_supplement',
   '["특효","즉효","당장","바로 효과","100% 효과","부작용 없음",
     "의사 추천(허위)","임상 입증(허위)","천연 100%(미인증)",
     "질병 치료","질병 예방"]'),

  ('regulatory_keywords_cosmetics',
   '["여드름 치료","주름 제거","미백 효과","기미 제거","흉터 제거",
     "피부 재생","아토피 치료","건선 치료","리프팅 효과","성형 효과",
     "안티에이징(미인증)","주름 개선(미인증)"]'),

  ('regulatory_keywords_medical_device',
   '["치료","치유","의료","수술","처방","의사 진료 대체","질병 진단"]'),

  -- ── AI 디스클레이머 ──
  ('disclaimer_general',
   '⚠️ AI 생성 콘텐츠 안내\n본 글은 인공지능(Claude)이 자동 생성한 초안으로, 게시 전 사실관계와 표현을 반드시 검토해 주세요.'),

  ('disclaimer_regulated',
   '⚠️ 광고·표시 규정 안내 — 반드시 검토 필요\n본 글은 AI로 생성되었으며, 식품표시광고법 등 관련 법령에 따라 효능·효과·치료 관련 표현은 사용이 제한됩니다. 게시 전 반드시 표현을 검토하시기 바랍니다.\n\n본 콘텐츠는 의학적 조언을 대체하지 않습니다.'),

  -- ── 카테고리 → 디스클레이머 매핑 (확장 가능) ──
  ('disclaimer_category_map',
   '{"food":"regulated","baby_food":"regulated","health_supplement":"regulated","cosmetics":"regulated","medical_device":"regulated","general":"general"}'),

  -- ── 분량별 블로그 포인트 비용 (운영 중 조정 가능) ──
  ('blog_cost_500',  '20'),
  ('blog_cost_1000', '40'),
  ('blog_cost_2000', '80')

ON CONFLICT (key) DO NOTHING;


COMMIT;

-- 알림: PostgREST 스키마 캐시 갱신
NOTIFY pgrst, 'reload schema';
