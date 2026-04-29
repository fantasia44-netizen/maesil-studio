"""블로그 포스트 프롬프트"""
from services.claude_service import SYSTEM_BASE, build_brand_context


def build_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    brand_ctx = build_brand_context(brand)
    topic = input_data.get('topic', '')
    purpose = input_data.get('purpose', '정보제공')
    length = input_data.get('length', '2000')
    seo_keywords = input_data.get('seo_keywords', '')

    system = f"""{SYSTEM_BASE}

[브랜드 컨텍스트]
{brand_ctx}"""

    user = f"""다음 조건에 맞는 블로그 포스트를 작성해 주세요.

주제/키워드: {topic}
글 목적: {purpose}
분량: 약 {length}자
SEO 키워드: {seo_keywords or '없음'}

아래 형식으로 출력하세요:

## 제목 후보 (3개)
1. [제목1]
2. [제목2]
3. [제목3]

## 본문
[서론 → 본론 → 결론 순서로 작성]

## 태그
[태그1], [태그2], ... (10개)

## 메타 디스크립션
[160자 이내의 검색 스니펫]"""

    return system, user
