"""네이버/구글 세트 생성 결과 파싱 공용 헬퍼.

experience_task.py 에서 확립된 [[[NAVER]]]/[[[GOOGLE]]] 구분자 파싱 로직을
blog_text_task.py 등 다른 "네이버+구글 세트" 생성 태스크에서도 재사용하기 위해 추출.
"""
import logging

logger = logging.getLogger(__name__)


def split_naver_google(text: str, both: bool):
    """생성 원문 → (naver_text, google_text). 구분자 없으면 전체를 네이버판으로 폴백."""
    naver_text, google_text = text.replace('[[[NAVER]]]', '').strip(), ''
    if both and '[[[GOOGLE]]]' in text:
        head, _, tail = text.partition('[[[GOOGLE]]]')
        naver_text = head.replace('[[[NAVER]]]', '').strip()
        google_text = tail.strip()
    elif both:
        logger.warning('[text_split] 구분자 파싱 실패 — 전체를 네이버판으로 반환')
    return naver_text, google_text
