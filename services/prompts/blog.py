"""블로그 포스트 프롬프트 — 4축 입력 + 이력 관계 모드 + 카테고리별 안전망.

입력 모델 (input_data):
  topic            큰 주제 ("이유식재료")
  keyword          핵심 키워드 ("야채큐브")
  details          상세 지시 (선택, 강조 포인트)
  purpose          글 목적 (정보제공 | 구매유도 | 브랜드인지)
  angle            앵글 (information|review|timeline|comparison|qna|trend)
  length           '500' | '1000' | '2000'
  seo_keywords     쉼표 구분 SEO 키워드
  relation_mode    'new' | 'series' | 'variant' | 'ignore'

추가 컨텍스트 (build_prompt 호출자가 주입):
  product          (선택) products row
  category         (선택) 카테고리 키 — 시스템 금지어 매칭용
  merged_avoid_words (선택) 3-tier 합집합 금지어
  recent_creations (선택) [{title, topic, keyword, angle}] — 'new' 모드에서 회피용
  related_creation (선택) {title, output_text 일부} — 'series'/'variant' 일 때 참조 글
"""
from services.claude_service import SYSTEM_BASE, build_brand_context


_ANGLE_LABEL = {
    'information': '정보형 가이드 — 독자가 모르는 사실/방법을 친절히 설명',
    'review':      '후기형 — 실제 사용 시나리오·체감 위주',
    'timeline':    '시기별 — 월령/계절/단계별 변화에 따른 안내',
    'comparison':  '비교형 — 대안과의 비교, 선택 기준 제시',
    'qna':         'Q&A — 독자가 자주 묻는 질문 위주',
    'trend':       '트렌드 — 최신 이슈/유행과 연결',
}


def _angle_directive(angle: str) -> str:
    return _ANGLE_LABEL.get((angle or '').lower(), '정보형 가이드')


def _purpose_directive(purpose: str) -> str:
    p = (purpose or '').strip()
    if p == '구매유도':
        return ('전환 중심: 마지막 단락에서 구매·문의 행동을 자연스럽게 유도. '
                '단, 효능/치료 등 규제 표현 금지. 신뢰 근거(원료·제조·후기 등) 1줄 이상 포함.')
    if p == '브랜드인지':
        return ('브랜드 스토리 톤: 제품 사양보다 가치·관점·철학 중심. '
                '광고색은 강하되 정보형 신뢰 자산 1~2개는 반드시 포함.')
    return ('정보 제공이 주: 본문 80% 정보, 20% 자연스러운 브랜드 노출. '
            '독자가 검색 의도(정보 학습)를 충족시키지 못하면 SEO 강등됨.')


def _length_directive(length: str) -> tuple[str, int]:
    """분량 옵션 → (지시문, max_tokens)."""
    s = str(length or '1000')
    if s == '500':
        return ('약 500자 — 짧고 핵심만. 서론 2줄 + 본문 3개 항목 + 결론 1줄.', 2048)
    if s == '2000':
        return ('약 2,000자 — 롱폼 SEO. 서론 3~4줄 + 본문 H3 5~7개 + 결론 + Q&A 1~2개. '
                '구체 수치/예시/단계별 가이드 포함하여 검색 1페이지 점유 가능 수준의 깊이.', 6000)
    return ('약 1,000자 — 표준 SEO 블로그. 서론 + 본문 H3 4~5개 + 결론.', 4000)


def _format_recent_titles(recent_creations: list[dict] | None) -> str:
    if not recent_creations:
        return ''
    lines = []
    for c in recent_creations[:30]:
        t = (c.get('title') or '').strip()
        topic = (c.get('topic') or '').strip()
        kw = (c.get('keyword') or '').strip()
        ang = (c.get('angle') or '').strip()
        bits = [t or f'{topic} × {kw}']
        meta = ' / '.join(b for b in [ang, kw] if b)
        if meta:
            bits.append(f'({meta})')
        lines.append(f'  - {" ".join(bits)}')
    return '\n'.join(lines)


def _relation_directive(mode: str,
                        recent_creations: list[dict] | None,
                        related_creation: dict | None) -> str:
    """이력 관계 모드별 지시문."""
    m = (mode or 'new').lower()
    if m == 'series':
        if not related_creation:
            return ''
        ref_title = (related_creation.get('title') or '이전 글').strip()
        excerpt = (related_creation.get('excerpt') or '').strip()
        block = f'''[시리즈 후속편 모드]
이 글은 다음 이전 글의 후속편입니다.
- 이전 글 제목: {ref_title}
- 이전 글 발췌: {excerpt[:600]}

지시:
1. 이전 글의 톤·관점·용어를 그대로 이어가시오.
2. 같은 내용 반복 금지. 이전 글이 다루지 못한 심화/다음 단계 내용으로 작성.
3. 첫 단락에 "지난 글에서는 ~ 다뤘습니다. 이번 글에서는 ~" 형태의 자연스러운 연결 1줄 포함.'''
        return block

    if m == 'variant':
        if not related_creation:
            return ''
        ref_title = (related_creation.get('title') or '원본 글').strip()
        excerpt = (related_creation.get('excerpt') or '').strip()
        block = f'''[변형/재가공 모드]
원본 글과 같은 주제를, 새로운 각도와 표현으로 재작성합니다.
- 원본 제목: {ref_title}
- 원본 발췌: {excerpt[:600]}

지시:
1. 주제·핵심 메시지는 동일. 도입부·본문 구조·예시·문장은 모두 새롭게.
2. 원본 문장을 그대로 차용하지 마시오.
3. 결과 첫 줄에 사용된 새로운 앵글을 한 줄로 명시.'''
        return block

    if m == 'ignore':
        return ''

    # 기본: 'new' — 이력 회피
    listed = _format_recent_titles(recent_creations)
    if not listed:
        return ''
    return f'''[다양성 모드 — 이력 회피]
이미 작성한 다음 글들과 주제·각도·도입부가 겹치지 않게 새로운 시각에서 작성하시오:
{listed}

지시:
1. 위 목록의 제목/표현/구성 패턴을 반복하지 마시오.
2. 같은 키워드라도 다른 앵글(시기별/비교형/Q&A 등)로 풀어내시오.'''


# ─────────────────────────────────────────────────────────────
# 메인 빌더
# ─────────────────────────────────────────────────────────────

def build_prompt(brand: dict, input_data: dict,
                 *,
                 product: dict | None = None,
                 category: str | None = None,
                 merged_avoid_words: list[str] | None = None,
                 recent_creations: list[dict] | None = None,
                 related_creation: dict | None = None) -> tuple[str, str, int]:
    """블로그 프롬프트 빌드 → (system, user, max_tokens)."""
    topic        = (input_data.get('topic') or '').strip()
    keyword      = (input_data.get('keyword') or '').strip()
    details      = (input_data.get('details') or '').strip()
    purpose      = (input_data.get('purpose') or '정보제공').strip()
    angle        = (input_data.get('angle') or 'information').strip()
    length       = str(input_data.get('length') or '1000')
    seo_keywords = (input_data.get('seo_keywords') or '').strip()
    relation_mode = (input_data.get('relation_mode') or 'new').strip()

    brand_ctx = build_brand_context(brand, product=product,
                                    merged_avoid_words=merged_avoid_words)
    angle_dir = _angle_directive(angle)
    purpose_dir = _purpose_directive(purpose)
    length_dir, max_tokens = _length_directive(length)
    relation_dir = _relation_directive(relation_mode, recent_creations, related_creation)

    system = f"""{SYSTEM_BASE}

[브랜드 컨텍스트]
{brand_ctx}

[작성 원칙]
- 한국 검색엔진(네이버/구글) SEO 최적화. 메인 키워드는 첫 100자 + H2/H3 헤딩에 자연스럽게 등장.
- 제목 후보 3개는 서로 다른 후킹 패턴(숫자형/질문형/비교형/감성형 중 다른 3종) 사용.
- 브랜드를 언급할 때 1인칭/직접 화법 사용 (예: '{(brand or {}).get('name','')}는 ~'). '~브랜드들' 같은 3인칭 회피.
- 마크다운 형식. 본문 길이 지시를 엄수.
- {purpose_dir}
- {angle_dir} 으로 작성.
- {length_dir}
"""

    user_parts = [f'''다음 조건의 블로그 포스트를 작성해 주세요.

[입력]
- 주제: {topic or '(미지정)'}
- 핵심 키워드: {keyword or '(미지정)'}
- 글 목적: {purpose}
- 앵글: {angle}
- 분량: {length}자
- SEO 키워드: {seo_keywords or '(미지정)'}''']

    if details:
        user_parts.append(f'- 상세 지시: {details}')

    if relation_dir:
        user_parts.append('')
        user_parts.append(relation_dir)

    user_parts.append('''
[출력 형식 — 반드시 준수]

## 제목 후보 (3개)
1. [제목1 — 패턴1]
2. [제목2 — 패턴2]
3. [제목3 — 패턴3]

## 본문
[서론 → 본문(H3) → 결론 순서. 분량 지시 엄수. 마크다운.]

## 태그
[태그1], [태그2], ... (10개, 검색량 있는 키워드 우선)

## 메타 디스크립션
[140~160자, 메인 키워드 포함, 클릭 유도 후킹 1줄 + 핵심 가치 1줄]''')

    user = '\n'.join(user_parts)
    return system, user, max_tokens
