"""매요 AI 클라이언트

우선순위:
  1. maesil-agency 서버 (maeyo_agency_url + maeyo_cs_token 설정 시)
  2. 로컬 Claude 폴백 (Anthropic API만 있으면 동작)
"""
from __future__ import annotations

import json
import logging
import re
import uuid

import requests

logger = logging.getLogger(__name__)
_TIMEOUT = 20


# ════════════════════════════════════════════════════════
# 설정 & 라우팅
# ════════════════════════════════════════════════════════

def _get_connection() -> tuple[str, str]:
    """(agency_url, cs_token) — DB 또는 환경변수에서 읽기."""
    from services.config_service import get_config
    return get_config('maeyo_agency_url'), get_config('maeyo_cs_token')


def chat(
    message: str,
    *,
    history: list[dict] | None = None,
    user_context: dict | None = None,
    operator_id: str = "",
    user_id: str = "",
    conversation_id: str | None = None,
    program: str = "maesil-studio",
) -> dict:
    """
    maesil-agency 또는 로컬 Claude로 채팅.

    Returns:
        {reply, emotion, action, hint, layer, conversation_id, message_id}
    """
    agency_url, cs_token = _get_connection()

    # ── 1) maesil-agency 연결 (설정된 경우)
    if agency_url:
        return _agency_chat(
            agency_url=agency_url,
            cs_token=cs_token,
            message=message,
            history=history or [],
            user_context=user_context or {},
            operator_id=operator_id,
            user_id=user_id,
            conversation_id=conversation_id,
            program=program,
        )

    # ── 2) 로컬 Claude 폴백 (agency 미설정 시 자동 사용)
    return _local_claude_chat(
        message=message,
        history=history or [],
        user_context=user_context or {},
        conversation_id=conversation_id,
    )


# ════════════════════════════════════════════════════════
# Agency 모드
# ════════════════════════════════════════════════════════

def _agency_chat(
    agency_url: str, cs_token: str,
    message: str, history: list, user_context: dict,
    operator_id: str, user_id: str,
    conversation_id: str | None, program: str,
) -> dict:
    url = agency_url.rstrip('/') + '/api/cs/chat'
    payload = {
        "message":         message,
        "history":         history,
        "user_context":    user_context,
        "operator_id":     operator_id or "",
        "user_id":         user_id or "",
        "program":         program,
        "conversation_id": conversation_id,
    }
    headers = {
        "Content-Type": "application/json",
        "X-CS-Token":   cs_token or "",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        logger.warning("[maeyo_client] agency 연결 실패 (%s) — 로컬 폴백 시도", url)
        return _local_claude_chat(message, history, user_context, conversation_id)
    except requests.exceptions.Timeout:
        logger.warning("[maeyo_client] agency 응답 시간 초과 — 로컬 폴백 시도")
        return _local_claude_chat(message, history, user_context, conversation_id)
    except Exception as e:
        logger.error("[maeyo_client] agency chat 오류: %s — 로컬 폴백 시도", e)
        return _local_claude_chat(message, history, user_context, conversation_id)


# ════════════════════════════════════════════════════════
# 로컬 Claude 폴백 모드
# ════════════════════════════════════════════════════════

# 기능별 페이지 URL (action 버튼용)
_PAGE_ACTIONS: list[tuple[list[str], str, str]] = [
    (['블로그', '포스트', '글쓰기'],           '블로그 바로가기',        '/create/blog'),
    (['인스타', '인스타그램', '피드'],          '인스타 바로가기',        '/create/instagram'),
    (['쇼츠', '릴스', '영상'],                  '쇼츠 만들기',            '/create/shorts'),
    (['배너', '이미지 배너', '광고 배너'],       '배너 만들기',            '/create/banner'),
    (['제안서', '홍보물', '카탈로그', '리플릿'], '홍보물 만들기',          '/create/promo'),
    (['상세페이지', '상세 페이지'],             '상세페이지 빌더',         '/create/detail_page_builder'),
    (['브랜드', '브랜드 관리'],                 '브랜드 관리',            '/brand'),
    (['상품', '상품 관리'],                     '상품 관리',              '/product'),
    (['포인트', '잔액', '구독'],               '구독·포인트 관리',        '/billing'),
    (['이력', '생성 이력', '히스토리'],         '생성 이력 보기',          '/history'),
]

_SYSTEM_PROMPT = """당신은 **매요**입니다 — 매실 스튜디오의 친절한 AI 도우미예요.
반말이 아닌 친근한 존댓말(~요, ~해요)을 사용하고, 이모지를 적절히 활용해 대화를 밝게 만들어요.

## 매실 스튜디오란?
소상공인·브랜드를 위한 AI 마케팅 콘텐츠 자동 생성 SaaS입니다.

## 주요 기능
- **블로그 포스트 생성** (/create/blog): SEO 블로그 글 자동 작성, 500~2000자
- **인스타그램 콘텐츠** (/create/instagram): 릴스/피드 이미지+캡션 생성
- **쇼츠/릴스 영상** (/create/shorts): AI 대본 → FLUX 이미지 → TTS 나레이션 → MP4 영상
- **배너 만들기** (/create/banner): 인스타/유튜브/카카오/스마트스토어 등 배너 이미지
- **홍보물 만들기** (/create/promo): 제안서, 협찬 제안서, 카탈로그, 리플릿, 전단지
- **상세페이지 빌더** (/create/detail_page_builder): 스마트스토어·쇼핑몰 상세페이지
- **브랜드 관리** (/brand): 브랜드 프로필, 로고, 브랜드 키트
- **상품 관리** (/product): 상품 등록, 이미지 관리

## 포인트 시스템
- 블로그 40P / 인스타 30P / 쇼츠 영상 300P / 배너 80P
- 제안서 150P / 카탈로그 150~500P(분량별) / 리플릿 120P / 전단지 80P
- 포인트 부족 시 구독 플랜 업그레이드 또는 포인트 구매 가능 (/billing)

## 자주 묻는 것
- "왜 생성이 안 되나요?" → 포인트 잔액 확인, API 키 설정 확인 권장
- "쇼츠 영상 얼마나 걸려요?" → 보통 1~2분 (5씬 이미지+TTS+FFmpeg 조립)
- "배너 사이즈를 마음대로 할 수 있나요?" → 네, 커스텀 사이즈 직접 입력 가능
- "제품 이미지를 배너에 넣을 수 있나요?" → 상품 등록 후 배너 만들기에서 선택 가능

## 응답 규칙
- 간결하게 (3~5문장 이내)
- 링크나 페이지 이동이 필요하면 action에 명시
- 모르는 것은 솔직하게 모른다고 안내

다음 JSON 형식으로만 응답하세요:
{
  "reply": "실제 답변 (markdown 없이 순수 텍스트)",
  "emotion": "happy|thinking|helpful|sorry",
  "hint": "짧은 추가 팁 (없으면 null)",
  "action_label": "버튼 텍스트 (없으면 null)",
  "action_url": "이동할 경로 (없으면 null)"
}"""


def _local_claude_chat(
    message: str,
    history: list[dict],
    user_context: dict,
    conversation_id: str | None = None,
) -> dict:
    """Anthropic Claude를 직접 호출해 매요 AI 응답 생성."""
    from services.claude_service import generate_text

    # 대화 히스토리 → 프롬프트 포맷
    history_text = ''
    if history:
        lines = []
        for h in history[-6:]:  # 최근 6턴
            role = '사용자' if h.get('role') == 'user' else '매요'
            lines.append(f'{role}: {h.get("content", "")}')
        history_text = '\n'.join(lines) + '\n'

    # 사용자 컨텍스트 요약
    plan = user_context.get('plan_type', '')
    ctx_note = f'[사용자 플랜: {plan}]' if plan else ''

    user_prompt = f'{ctx_note}\n{history_text}사용자: {message}\n\n위 대화에 이어 매요로서 JSON으로 답변하세요.'

    try:
        raw = generate_text(_SYSTEM_PROMPT, user_prompt,
                            max_tokens=400, model='claude-haiku-4-5-20251001')
        return _parse_local_response(raw, conversation_id)
    except Exception as e:
        logger.error("[maeyo_client] 로컬 Claude 오류: %s", e)
        return _fallback("잠시 오류가 발생했어요. 다시 시도해 주세요. 😅", conversation_id)


def _parse_local_response(raw: str, conversation_id: str | None) -> dict:
    """Claude 응답(JSON 문자열) → 표준 dict로 변환."""
    cid = conversation_id or str(uuid.uuid4())
    mid = str(uuid.uuid4())

    # JSON 추출
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
    s, e = raw.find('{'), raw.rfind('}') + 1
    if s >= 0 and e > s:
        raw = raw[s:e]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # JSON 파싱 실패 시 전체를 reply로 사용
        return _fallback(raw[:300] if raw else '죄송해요, 다시 시도해 주세요.', conversation_id)

    reply       = str(data.get('reply', '죄송해요, 다시 시도해 주세요.'))
    emotion     = str(data.get('emotion', 'helpful'))
    hint        = data.get('hint') or None
    action_label= data.get('action_label') or None
    action_url  = data.get('action_url') or None

    # action_label은 있는데 url이 없으면 키워드 매핑 시도
    if action_label and not action_url:
        action_url = _guess_action_url(reply + ' ' + (action_label or ''))

    action = {'label': action_label, 'url': action_url} if (action_label and action_url) else None

    return {
        'reply':           reply,
        'emotion':         emotion,
        'action':          action,
        'hint':            hint,
        'layer':           'local_claude',
        'conversation_id': cid,
        'message_id':      mid,
    }


def _guess_action_url(text: str) -> str | None:
    """텍스트 키워드로 관련 페이지 URL 추측."""
    lower = text.lower()
    for keywords, _label, url in _PAGE_ACTIONS:
        if any(k in lower for k in keywords):
            return url
    return None


def _fallback(msg: str, conversation_id: str | None = None) -> dict:
    return {
        'reply':           msg,
        'emotion':         'thinking',
        'action':          None,
        'hint':            None,
        'layer':           'fallback',
        'conversation_id': conversation_id or str(uuid.uuid4()),
        'message_id':      str(uuid.uuid4()),
    }
