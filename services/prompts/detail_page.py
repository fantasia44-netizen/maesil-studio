"""상세페이지 카피 프롬프트"""
from services.claude_service import SYSTEM_BASE, build_brand_context


def build_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    brand_ctx = build_brand_context(brand)
    product_name = input_data.get('product_name', '')
    features = input_data.get('features', '')
    price_range = input_data.get('price_range', '')
    differentiator = input_data.get('differentiator', '')

    system = f"""{SYSTEM_BASE}

[브랜드 컨텍스트]
{brand_ctx}"""

    user = f"""상세페이지 카피를 작성해 주세요.

상품명/카테고리: {product_name}
핵심 기능/특징: {features}
가격대: {price_range or '미입력'}
경쟁 대비 차별점: {differentiator or '미입력'}

아래 형식으로 출력하세요:

## 상단 훅 문구
[강렬한 첫 인상 문구 — 2~3줄]

## 기능별 소제목 + 설명 카피
### [기능1 소제목]
[설명 2~3줄]

### [기능2 소제목]
[설명 2~3줄]

(입력된 특징 수만큼 반복)

## 고객 후기 포맷 (예시 3개)
⭐⭐⭐⭐⭐ [닉네임]
[자연스러운 후기 2~3줄]

## CTA 문구 (5개)
1. [문구]
2. [문구]
...

## FAQ (5개)
Q. [질문]
A. [답변]"""

    return system, user
