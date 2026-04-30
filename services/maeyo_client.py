"""매요 AI 클라이언트 — maesil-agency /api/cs/chat 호출.

agency URL과 CS 토큰은 saas_config DB에서 읽음 (config_service.get_config).
"""
from __future__ import annotations

import logging
import uuid
import requests

logger = logging.getLogger(__name__)
_TIMEOUT = 20


def _get_connection() -> tuple[str, str]:
    """(agency_url, cs_token) — DB에서 읽기."""
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
    maesil-agency POST /api/cs/chat 호출.

    Returns:
        {reply, emotion, action, hint, layer, conversation_id, message_id}
        오류 시 fallback dict 반환.
    """
    agency_url, cs_token = _get_connection()

    if not agency_url:
        return _fallback("매요 AI 연결이 설정되지 않았습니다. 관리자에게 문의하세요.")

    url = agency_url.rstrip('/') + '/api/cs/chat'
    payload = {
        "message":         message,
        "history":         history or [],
        "user_context":    user_context or {},
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
        logger.warning("[maeyo_client] agency 연결 실패 (%s)", url)
        return _fallback("잠시 연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요.")
    except requests.exceptions.Timeout:
        logger.warning("[maeyo_client] agency 응답 시간 초과")
        return _fallback("응답이 지연되고 있어요. 잠시 후 다시 시도해 주세요.")
    except Exception as e:
        logger.error("[maeyo_client] chat 오류: %s", e)
        return _fallback("일시적인 오류가 발생했어요. 잠시 후 다시 시도해 주세요.")


def _fallback(msg: str) -> dict:
    return {
        "reply":           msg,
        "emotion":         "thinking",
        "action":          None,
        "hint":            None,
        "layer":           "fallback",
        "conversation_id": str(uuid.uuid4()),
        "message_id":      str(uuid.uuid4()),
    }
