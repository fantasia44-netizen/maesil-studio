"""blog_quality — 발행 전 AI 문체/품질 검사 (규칙 기반, LLM 불필요).

생성된 블로그 본문을 분석해 AI 흔적·경험성·독창성 등을 점수화하고, 개선 포인트를
반환한다. 애드센스·구글 AI 탐지 리스크를 발행 전에 잡는 미들웨어.
"""
from __future__ import annotations

import re

# 상투 표현(= AI 흔적) — prompts/blog._ANTI_AI_TELL 금지 목록과 동기화
_CLICHES = [
    '결론적으로', '요약하자면', '알아보았습니다', '도움이 되시길',
    '급변하는 디지털', '현대 사회에서', '매우 중요한 요소',
    '필수적이라고 할 수 있', '한 단계 더 성장', '성공적인 비즈니스를 위해',
    '중요성이 커지고 있', '주목받고 있습니다', '빼놓을 수 없',
]

# 1인칭 실무 경험 신호
_EXPERIENCE_MARKERS = ['저는', '저희', '제가', '직접 운영', '운영하며', '운영하면서',
                       '실제로', '겪었', '경험상', '해보니', '돌려보니', '적용해',
                       '시행착오', '테스트']
_ACTION_MARKERS = ['체크리스트', '점검', '단계', 'step', '따라 하', '실행', '확인하세요',
                   '해보세요', '순서']


def _sentences(text: str) -> list[str]:
    plain = re.sub(r'[#>*`\-|]', ' ', text or '')
    return [s.strip() for s in re.split(r'[.!?。\n]', plain) if len(s.strip()) > 4]


def _count_any(text: str, needles: list[str]) -> int:
    low = (text or '').lower()
    return sum(low.count(n.lower()) for n in needles)


def analyze(text: str) -> dict:
    """본문 → {scores{}, issues[], ai_risk}. 점수 0~100(높을수록 좋음, ai_risk만 낮을수록 좋음)."""
    text = text or ''
    n_chars = len(text)
    sents = _sentences(text)
    n_sents = max(1, len(sents))
    issues: list[str] = []

    # 상투 표현
    cliche_hits = [c for c in _CLICHES if c in text]
    cliche_n = len(cliche_hits)
    if cliche_hits:
        issues.append(f'상투 표현 {cliche_n}종 감지: {", ".join(cliche_hits[:4])}')

    # 종결어미 반복 (습니다 / 할 수 있습니다)
    can_do = len(re.findall(r'할 수 있(습니다|어요|다)', text))
    seubnida = len(re.findall(r'습니다', text))
    if can_do >= 3:
        issues.append(f'"할 수 있습니다"류 반복 {can_do}회 — 종결 다양화 필요')

    # 문체 혼용 (존댓말 + 평서형 혼재)
    plain_end = len(re.findall(r'(?<![가-힣])[가-힣]+(?:다|된다|이다)\.', text))
    mixed = seubnida >= 2 and plain_end >= 2
    if mixed:
        issues.append('문체 혼용 감지(습니다체 + 평서형) — 한 문체로 통일 권장')

    # 소제목 과다
    heads = len(re.findall(r'(?m)^#{2,3}\s', text))
    head_ratio = heads / max(1, n_chars / 400)   # 400자당 소제목 수
    if head_ratio > 1.6:
        issues.append(f'소제목 과다({heads}개) — 기계적 구조로 보일 수 있음')

    # 경험/실행/수치 신호
    exp = _count_any(text, _EXPERIENCE_MARKERS)
    act = _count_any(text, _ACTION_MARKERS)
    nums = len(re.findall(r'\d[\d,]*\s*(원|％|%|배|개|건|회|명|일|주|개월|년)', text))
    if exp == 0:
        issues.append('1인칭 실무 경험 문장 0개 — 경험 문단 추가 권장(E-E-A-T)')
    if nums == 0:
        issues.append('구체적 수치 0개 — 사례·수치로 근거 보강 권장')

    # ── 점수화 ──
    def clamp(x): return max(0, min(100, int(x)))

    ai_risk = clamp(cliche_n * 22 + max(0, can_do - 2) * 10
                    + (25 if mixed else 0) + max(0, head_ratio - 1.6) * 20)
    experience = clamp(30 + exp * 12 + nums * 4)
    factuality = clamp(45 + nums * 8)
    actionability = clamp(35 + act * 14)
    originality = clamp(90 - cliche_n * 18 - (15 if mixed else 0) + min(20, exp * 4))

    return {
        'scores': {
            '경험성': experience,
            '독창성': originality,
            '사실성': factuality,
            '실행가능성': actionability,
            'AI문체위험도': ai_risk,
        },
        'ai_risk': ai_risk,
        'issues': issues,
        'stats': {'chars': n_chars, 'sentences': n_sents, 'headings': heads,
                  'cliches': cliche_n, 'experience_markers': exp, 'numbers': nums},
        'verdict': ('AI 글로 보일 위험 높음' if ai_risk >= 55
                    else '양호' if ai_risk <= 25 else '보통'),
    }
