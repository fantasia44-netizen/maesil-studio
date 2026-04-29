"""KST 시간 유틸리티"""
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt


def to_kst_str(value, fmt='%Y-%m-%d %H:%M') -> str:
    if not value:
        return ''
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        value = ensure_aware(value).astimezone(KST)
        return value.strftime(fmt)
    except Exception:
        return str(value)
