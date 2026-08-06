"""블로그 포스트 프롬프트 — 4축 입력 + 이력 관계 모드 + 카테고리별 안전망.

입력 모델 (input_data):
  topic            큰 주제 (예: "신제품 소개")
  keyword          핵심 키워드 (예: "브랜드명 + 제품명")
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


# ─────────────────────────────────────────────────────────────
# AI 탐지 회피 + E-E-A-T + 홍보 절제 (모든 브랜드 공통, 구글/애드센스 대응)
# GPT·Gemini 개선안의 공통 핵심을 프롬프트 레벨로 반영.
# ─────────────────────────────────────────────────────────────

_ANTI_AI_TELL = '''[AI 티 제거 — 반드시 준수]
- 서론을 백과사전식 정의나 "최근 ~시장이 성장하면서 ~가 중요해지고 있습니다"류 일반론으로 열지 마시오. 첫 3문장 안에 독자가 겪는 구체적 문제나 실제 상황·수치로 바로 진입하시오.
- 다음 상투 표현을 절대 쓰지 마시오: "결론적으로", "요약하자면", "~에 대해 알아보았습니다", "도움이 되시길 바랍니다", "급변하는 디지털 시대", "현대 사회에서", "매우 중요한 요소입니다", "필수적이라고 할 수 있습니다", "한 단계 더 성장할 수 있습니다", "성공적인 비즈니스를 위해".
- 한 글 안에서 종결어미(문체)를 섞지 마시오 — 존댓말이면 끝까지 존댓말("~습니다/~합니다"). "~습니다"와 "~된다/~이다"를 한 글에 혼용하는 것은 금지.
- "~할 수 있습니다"류 같은 종결을 3회 이상 반복하지 마시오. 문장 길이를 의도적으로 들쭉날쭉하게(긴 설명 뒤 짧은 단언 한 줄) 만드시오.
- 모든 문단을 "소제목 → 3~4줄 → 소제목"의 동일 패턴으로 찍어내지 마시오. 표·짧은 단언·질문을 섞어 리듬을 만드시오.
- 소제목(H3)을 기계적으로 촘촘히 달지 마시오. 논리상 필요할 때만.
- 어색한 번역투·런온 문장을 피하고, 한국어로 자연스럽게 읽히도록 다듬으시오.'''

_EEAT_GROUNDING = '''[경험·사실성 — 구글 E-E-A-T]
- 제공된 브랜드/컨텍스트에 없는 구체적 수치·성과·고객사례를 지어내지 마시오. 설명을 위해 예시 수치를 쓸 때는 "예시"임을 문장 안에 분명히 밝히시오.
- 이론적 정의보다 "실제 운영에서는 이렇게 달랐다"는 관점을 우선하고, 브랜드 톤에 맞는 1인칭 실무 문장을 최소 1개 포함하시오.
- 매출 같은 표면 숫자보다 이익·실행 가능성·실제 비용 관점을 우선하시오.'''

_CTA_RESTRAINT = '''[홍보 절제 — 애드센스 대응]
- 모든 글을 서비스 홍보로 끝내지 마시오. 글 목적이 정보제공이면 CTA는 생략하거나 "이런 작업이 번거로우면 별도 도구를 쓰는 방법도 있다" 정도로 약하게.
- "이 모든 문제는 OO로 해결됩니다"식 강매 문구 금지. 필요할 때만, 왜 그 도구가 필요한지 맥락으로 자연스럽게 연결하시오.'''


def _experience_directive(experience_block: str) -> str:
    """운영자 실제 경험 데이터가 있으면 근거로 강제 주입."""
    block = (experience_block or '').strip()
    if not block:
        # 경험 데이터가 없으면 날조 방지 규칙만 강조
        return ('\n[경험 데이터 없음]\n'
                '- 참고할 실제 경험 데이터가 제공되지 않았다. 구체적 성과 수치·고객사례를 지어내지 말고, '
                '일반적인 설명임이 드러나게 작성하시오(예시 수치는 "예시"로 명시).')
    return f'''
[운영자 실제 경험 데이터 — 본문 근거로 활용]
아래는 이 브랜드 운영자가 실제로 겪은 경험이다. 본문의 핵심 논리를 이 경험으로 뒷받침하시오.
{block}

지시:
1. 위 경험의 문제→조치→결과 흐름을 본문 한 곳 이상에서 1인칭 실무 사례로 자연스럽게 녹여라.
2. "수치(사용 가능)"에 있는 숫자만 사용하고, 여기 없는 성과 수치는 새로 지어내지 마라.
3. 익명 처리 지시가 있는 항목은 회사명/고객사명을 노출하지 마라.'''


def _product_ref_directive(product_ref_block: str) -> str:
    """연결된 실제 상품 정보가 있으면 사실 근거로 참조(스펙 날조 금지)."""
    block = (product_ref_block or '').strip()
    if not block:
        return ''
    return f'''
[연결된 실제 상품 — 사실 근거로 참조]
아래는 이 브랜드가 실제로 판매 중인 상품이다(매실인사이트 연동).
{block}

지시:
1. 상품을 언급할 때 위 실제 정보(이름·가격·특징)만 사용하고, 없는 사양·효능을 지어내지 마라.
2. 억지로 모든 상품을 넣지 말고, 주제와 맞는 상품만 자연스럽게 예로 들어라.'''


def _internal_links_directive(internal_links_block: str) -> str:
    """관련 발행글이 있으면 본문에 자연스러운 내부 링크로 삽입."""
    block = (internal_links_block or '').strip()
    if not block:
        return ''
    return f'''
[내부 링크 — 관련 발행글]
아래는 이미 발행된 관련 글이다. 본문 흐름상 자연스러운 위치에 2~4개를 마크다운 링크로 삽입하시오.
{block}

지시:
1. 억지로 다 넣지 말고, 문맥상 실제로 도움 되는 글만 골라 2~4개.
2. "여기를 클릭" 같은 앵커 대신, 글 제목의 핵심어를 앵커 텍스트로.
3. 한 문단에 링크를 몰아넣지 말고 본문 곳곳에 분산.'''


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
        return ('약 500자 — 짧고 핵심만. 서론 2줄 + 본문 3개 항목 + 결론 1줄.', 1500)
    if s == '2000':
        return ('약 2,000자 — 롱폼 SEO. 서론 3~4줄 + 본문 H3 5~7개 + 결론 + Q&A 1~2개. '
                '구체 수치/예시/단계별 가이드 포함하여 검색 1페이지 점유 가능 수준의 깊이.', 4000)
    return ('약 1,000자 — 표준 SEO 블로그. 서론 + 본문 H3 4~5개 + 결론.', 2500)


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

def _both_targets_output_rule(topic: str, keyword: str) -> str:
    """'네이버+구글(워드프레스) 세트' 출력 형식 — [[[NAVER]]]/[[[GOOGLE]]] 구분자로 두 판 요청.

    구글판 라벨(SEO 제목/메타 설명/슬러그/본문/FAQ/태그)은
    blueprints/integrations.py::_parse_google_post() 가 그대로 파싱하는 포맷과 일치시킨다.
    """
    return f'''
[출력 형식 — 반드시 준수. 아래 두 판을 모두 작성하고, 이 구분자를 정확히 그대로 사용]

[[[NAVER]]]
## 제목 후보 (3개)
1. [제목1 — 패턴1]
2. [제목2 — 패턴2]
3. [제목3 — 패턴3]

## 본문
[서론 → 본문(H3) → 결론 순서. 분량 지시 엄수. 마크다운.]

## 태그
#키워드1 #키워드2 … (10개, 검색량 있는 키워드 우선. 각 태그는 띄어쓰기 없이 한 단어로 붙여 쓰고 맨 앞에 #를 붙인다. 쉼표 없이 공백으로 구분. 예: #스마트스토어재등록 #네이버쇼핑노출 #상품등록전략)

## 메타 디스크립션
[140~160자, 메인 키워드 포함, 클릭 유도 후킹 1줄 + 핵심 가치 1줄]

[[[GOOGLE]]]
워드프레스(구글 검색용) 판. 네이버판보다 확실히 길고 깊게 — 배경 설명·상세 팁을 보강한다.
마크다운 사용. 순서:
  SEO 제목: (60자 이내, 핵심 검색어 앞배치, "{keyword or topic}" 반영)
  메타 설명: (150자 이내)
  슬러그: (영문 소문자-하이픈)
  본문: ## / ### 소제목으로 구조화. 비교·정리 가능한 정보는 마크다운 표 1개 이상으로 정리.
  FAQ: 독자가 검색할 질문 3~5개를 ### 질문 + 답변으로.
  태그: 쉼표로 구분한 키워드 8~10개.
  중요: 주제는 같아도 도입·소제목 구성·문장을 네이버판과 30~50% 이상 다르게 쓴다.
  네이버판 문장을 그대로 재사용하지 않는다(검색엔진 중복 콘텐츠 회피).'''


def _google_only_output_rule(topic: str, keyword: str) -> str:
    """'구글(워드프레스)만' 출력 형식 — 네이버판 없이 구글판 하나만 요청.

    라벨 포맷은 _both_targets_output_rule의 [[[GOOGLE]]] 섹션과 동일
    (blueprints/integrations.py 대신 services/wordpress_publish.py::parse_google_post
    가 그대로 파싱).
    """
    return f'''
[출력 형식 — 반드시 준수]
워드프레스(구글 검색용) 판. 마크다운 사용. 순서:
  SEO 제목: (60자 이내, 핵심 검색어 앞배치, "{keyword or topic}" 반영)
  메타 설명: (150자 이내)
  슬러그: (영문 소문자-하이픈)
  본문: ## / ### 소제목으로 구조화. 비교·정리 가능한 정보는 마크다운 표 1개 이상으로 정리.
  FAQ: 독자가 검색할 질문 3~5개를 ### 질문 + 답변으로.
  태그: 쉼표로 구분한 키워드 8~10개.'''


def build_prompt(brand: dict, input_data: dict,
                 *,
                 product: dict | None = None,
                 category: str | None = None,
                 merged_avoid_words: list[str] | None = None,
                 recent_creations: list[dict] | None = None,
                 related_creation: dict | None = None,
                 experience_block: str = '',
                 internal_links_block: str = '',
                 product_ref_block: str = '',
                 targets: str = 'naver') -> tuple[str, str, int]:
    """블로그 프롬프트 빌드 → (system, user, max_tokens).

    targets: 'naver'(기본, 네이버판만) | 'google'(구글판만) | 'both'(둘 다,
    [[[NAVER]]]/[[[GOOGLE]]] 구분자로 함께 요청). 'google'/'both' 모두 구글판이
    네이버판보다 길고 깊어 max_tokens 를 늘린다.
    """
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
    # 프롬프트 과다 방지 — brand_ctx 800자 초과 시 잘라냄 (출력 속도 개선)
    if len(brand_ctx) > 800:
        brand_ctx = brand_ctx[:800] + '\n...(이하 생략)'
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

{_ANTI_AI_TELL}

{_EEAT_GROUNDING}

{_CTA_RESTRAINT}
{_experience_directive(experience_block)}
{_product_ref_directive(product_ref_block)}
{_internal_links_directive(internal_links_block)}
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

    targets_mode = (targets or 'naver').strip().lower()
    if targets_mode == 'both':
        user_parts.append(_both_targets_output_rule(topic, keyword))
        max_tokens = int(max_tokens * 2.2)   # 구글판(더 긴 분량) 출력 여유
    elif targets_mode == 'google':
        user_parts.append(_google_only_output_rule(topic, keyword))
        max_tokens = int(max_tokens * 1.5)   # 구글판 하나만이라도 네이버판보다 깊게
    else:
        user_parts.append('''
[출력 형식 — 반드시 준수]

## 제목 후보 (3개)
1. [제목1 — 패턴1]
2. [제목2 — 패턴2]
3. [제목3 — 패턴3]

## 본문
[서론 → 본문(H3) → 결론 순서. 분량 지시 엄수. 마크다운.]

## 태그
#키워드1 #키워드2 … (10개, 검색량 있는 키워드 우선. 각 태그는 띄어쓰기 없이 한 단어로 붙여 쓰고 맨 앞에 #를 붙인다. 쉼표 없이 공백으로 구분. 예: #스마트스토어재등록 #네이버쇼핑노출 #상품등록전략)

## 메타 디스크립션
[140~160자, 메인 키워드 포함, 클릭 유도 후킹 1줄 + 핵심 가치 1줄]''')

    user = '\n'.join(user_parts)
    return system, user, max_tokens
