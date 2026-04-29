"""썸네일 문구 / 광고 카피 프롬프트"""
from services.claude_service import SYSTEM_BASE, build_brand_context


def build_thumbnail_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    brand_ctx = build_brand_context(brand)
    subject = input_data.get('subject', '')
    emphasis = input_data.get('emphasis', '')
    channel = input_data.get('channel', '유튜브')

    system = f"""{SYSTEM_BASE}

[브랜드 컨텍스트]
{brand_ctx}"""

    user = f"""썸네일 문구를 작성해 주세요.

상품/콘텐츠명: {subject}
강조 포인트: {emphasis}
채널: {channel}

아래 형식으로 출력하세요:

## 메인 문구 5개 (클릭률 최적화)
A. [문구] — [서브문구] [이모지 추천]
B. [문구] — [서브문구] [이모지 추천]
C. [문구] — [서브문구] [이모지 추천]
D. [문구] — [서브문구] [이모지 추천]
E. [문구] — [서브문구] [이모지 추천]

## 작성 의도
각 문구별 클릭을 유도하는 심리적 포인트를 한 줄씩 설명해 주세요."""

    return system, user


def build_ad_copy_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    brand_ctx = build_brand_context(brand)
    product = input_data.get('product', '')
    target = input_data.get('target', '')
    goal = input_data.get('goal', '구매')
    platform = input_data.get('platform', '인스타그램')

    system = f"""{SYSTEM_BASE}

[브랜드 컨텍스트]
{brand_ctx}"""

    user = f"""광고 카피 세트(5종)를 작성해 주세요.

상품/서비스: {product}
타겟 고객: {target or '브랜드 기본 타겟'}
광고 목표: {goal}
플랫폼: {platform}

아래 형식으로 출력하세요:

## 광고 카피 세트

### 1. 감성 소구형
헤드라인: [문구]
본문: [2~3줄]
CTA: [행동 유도 문구]

### 2. 혜택/가격 소구형
헤드라인: [문구]
본문: [2~3줄]
CTA: [행동 유도 문구]

### 3. 사회적 증거형
헤드라인: [문구]
본문: [2~3줄]
CTA: [행동 유도 문구]

### 4. 문제 해결형
헤드라인: [문구]
본문: [2~3줄]
CTA: [행동 유도 문구]

### 5. 긴급/희소성형
헤드라인: [문구]
본문: [2~3줄]
CTA: [행동 유도 문구]"""

    return system, user


def build_brand_kit_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    brand_ctx = build_brand_context(brand)
    extra = input_data.get('extra', '')

    system = f"""{SYSTEM_BASE}

[브랜드 컨텍스트]
{brand_ctx}"""

    user = f"""브랜드 정체성 패키지를 작성해 주세요.
{f'추가 방향성: {extra}' if extra else ''}

## 브랜드 슬로건 (5개)
1. [슬로건]
...

## 브랜드 스토리 (200자)
[내용]

## 핵심 메시지 (채널별 3개)
- 온라인 쇼핑몰: [메시지]
- SNS: [메시지]
- 오프라인/패키지: [메시지]

## 톤앤매너 가이드
- 사용할 어투: [예시]
- 피할 표현: [예시]
- 추천 형용사: [5개]

## 추천 해시태그 마스터 세트
대표 태그 (5개): #[태그] ...
카테고리 태그 (10개): #[태그] ...
브랜드 전용 태그 (3개): #[태그] ...

## 경쟁사 대비 포지셔닝 문구
[2~3줄]"""

    return system, user
