"""number_verify — 생성된 글의 숫자가 '근거(팩트 소스)'에 있는지 대조.

허위광고 방지용. 본문·표·FAQ·메타에 나오는 금액/퍼센트/배수를 뽑아, 근거
텍스트(이미지에서 읽은 값·경험 데이터)에 실제로 있는 값인지 확인한다. 포맷
차이(607만 vs 6,072,309원)를 흡수하려 정규화 후 오차범위로 비교하고, 근거에서
못 찾은 값을 '확인 필요' 목록으로 돌려준다. (틀림 판정이 아니라 사람 검토용 플래그)
"""
import re

# 억(+선택 만) 복합 → 하나의 값으로.  예) "1억 1,005만" = 110,050,000
_EOK_RE = re.compile(r'([0-9][0-9,]*)\s*억(?:\s*([0-9][0-9,]*)\s*만)?')
_MAN_RE = re.compile(r'([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:만원|만)')
_WON_RE = re.compile(r'([0-9][0-9,]*)\s*원')
_PCT_RE = re.compile(r'([0-9][0-9,]*(?:\.[0-9]+)?)\s*%')
_X_RE = re.compile(r'([0-9][0-9,]*(?:\.[0-9]+)?)\s*배')


def _num(s):
    try:
        return float((s or '').replace(',', ''))
    except Exception:
        return None


def _extract(text):
    """text → (금액 set[won], 퍼센트 set[float], 배수 set[float])."""
    text = text or ''
    money, pct, x = set(), set(), set()

    # 1) 억 복합 먼저 잡고, 그 구간을 공백으로 치환해 중복 추출 방지
    def _eok(m):
        won = (_num(m.group(1)) or 0) * 10 ** 8
        if m.group(2):
            won += (_num(m.group(2)) or 0) * 10 ** 4
        money.add(round(won))
        return ' ' * (m.end() - m.start())
    rest = _EOK_RE.sub(_eok, text)

    # 2) 남은 만/만원
    for m in _MAN_RE.finditer(rest):
        v = _num(m.group(1))
        if v is not None:
            money.add(round(v * 10 ** 4))
    # 3) 순수 원(만·억이 아닌)
    for m in _WON_RE.finditer(rest):
        v = _num(m.group(1))
        if v is not None:
            money.add(round(v))

    for m in _PCT_RE.finditer(text):
        v = _num(m.group(1))
        if v is not None:
            pct.add(v)
    for m in _X_RE.finditer(text):
        v = _num(m.group(1))
        if v is not None:
            x.add(v)
    return money, pct, x


def _near(val, pool, rel=0.0, ab=0.0):
    for s in pool:
        if abs(val - s) <= max(ab, max(abs(val), abs(s), 1) * rel):
            return True
    return False


def _fmt_money(won):
    won = int(won)
    if won >= 10 ** 8:
        eok, man = won // 10 ** 8, (won % 10 ** 8) // 10 ** 4
        return f'{eok}억' + (f' {man:,}만' if man else '') + '원'
    if won >= 10 ** 4:
        return f'{won // 10 ** 4:,}만원'
    return f'{won:,}원'


def _fmt(v):
    return str(int(v)) if float(v).is_integer() else str(v)


def verify(draft_text, source_text):
    """근거에서 확인 안 되는 숫자 목록(문자열) 반환. 없으면 []."""
    d_money, d_pct, d_x = _extract(draft_text)
    s_money, s_pct, s_x = _extract(source_text)
    flags = []
    # 금액: 5% 오차 (607만 vs 6,072,309원 흡수) · 만원 미만 잔돈은 무시
    for v in sorted(d_money):
        if v >= 10 ** 4 and not _near(v, s_money, rel=0.05):
            flags.append(_fmt_money(v))
    # 퍼센트: 10% 오차 (500%대≈539% 반올림 허용 · 75%·50% 날조는 잡음)
    for v in sorted(d_pct):
        if not _near(v, s_pct, rel=0.10, ab=1):
            flags.append(f'{_fmt(v)}%')
    # 배수: ±0.3
    for v in sorted(d_x):
        if not _near(v, s_x, ab=0.3):
            flags.append(f'{_fmt(v)}배')
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for f in flags:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out
