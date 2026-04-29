"""인스타그램 캡션 프롬프트"""
from services.claude_service import SYSTEM_BASE, build_brand_context


def build_prompt(brand: dict, input_data: dict) -> tuple[str, str]:
    brand_ctx = build_brand_context(brand)
    content = input_data.get('content', '')
    image_desc = input_data.get('image_desc', '')
    event = input_data.get('event', '')

    system = f"""{SYSTEM_BASE}

[브랜드 컨텍스트]
{brand_ctx}"""

    user = f"""인스타그램 게시물 캡션과 해시태그를 작성해 주세요.

게시 목적/내용: {content}
이미지 설명: {image_desc or '없음'}
이벤트/할인: {event or '없음'}

아래 형식으로 출력하세요:

## 캡션 (3가지 버전)

### 짧은 버전 (3~5줄)
[내용]

### 중간 버전 (5~8줄)
[내용]

### 긴 버전 (8~12줄)
[내용]

## 해시태그 (30개)
인기 해시태그 15개:
#[태그1] #[태그2] ...

틈새 해시태그 15개:
#[태그1] #[태그2] ...

## 최적 게시 시간 추천
[요일/시간대 + 이유]"""

    return system, user
