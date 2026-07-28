-- 014: 발행 콘텐츠 인덱스 (내부 링크 자동 추천용)
--
-- 워드프레스에 발행된 글의 제목/URL/키워드를 저장해, 새 글 생성 시 관련 글을
-- 검색해 본문에 자연스러운 내부 링크(2~4개)로 제안한다. 체류시간·SEO 강화.
-- 실행 위치: 매실스튜디오 Supabase (public 스키마)

CREATE TABLE IF NOT EXISTS published_content_index (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  brand_id          uuid,
  wp_post_id        bigint,
  title             text NOT NULL,
  url               text NOT NULL,
  slug              text,
  summary           text,
  keywords          text[] DEFAULT '{}',
  category          text,
  published_at      timestamptz DEFAULT now(),
  -- Search Console 성과(옵션, 03/4단계에서 갱신)
  sc_impressions    integer,
  sc_clicks         integer,
  sc_avg_position   numeric,
  updated_at        timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS published_content_brand_idx
  ON published_content_index (brand_id);
CREATE UNIQUE INDEX IF NOT EXISTS published_content_url_uidx
  ON published_content_index (url);
CREATE INDEX IF NOT EXISTS published_content_keywords_idx
  ON published_content_index USING gin (keywords);
