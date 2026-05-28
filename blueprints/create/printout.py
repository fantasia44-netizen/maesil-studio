"""인쇄물 생성 — 카탈로그 / 리플릿 / 전단지.

라우트:
  GET  /create/printout            폼 페이지
  POST /create/printout/generate   생성 요청 (AJAX)
"""
from __future__ import annotations

import logging

from flask import render_template, request, jsonify
from flask_login import login_required

from blueprints.create import create_bp
from blueprints.create._base import get_accessible_brands
from blueprints.create._promo_base import (
    run_promo_generation, CATALOG_PAGE_COSTS, catalog_type_guide,
)
from models import POINT_COSTS

logger = logging.getLogger(__name__)


# ── 유형별 메타 ───────────────────────────────────────────────
PRINTOUT_TYPES = {
    'catalog': {
        'label':    '카탈로그',
        'icon':     'bi-journal-richtext',
        'color':    '#198754',
        'desc':     '페이지별 카피 초안 — Canva·미리캔버스에 바로 붙여 편집',
        'target_placeholder': '예: 도매 바이어, 수출 파트너, 전시회 방문객',
        'extra_placeholder':  '예: 포함할 상품 라인, 특장점, 인증 현황, 연락처 등',
        'has_page_select': True,
    },
    'leaflet': {
        'label':    '리플릿',
        'icon':     'bi-newspaper',
        'color':    '#6f42c1',
        'desc':     '3단 접이 6패널 카피 — 디자인 툴에 패널별로 바로 적용',
        'target_placeholder': '예: 박람회 방문객, 매장 고객',
        'extra_placeholder':  '예: 행사명, 날짜, 특가·혜택 내용 등',
        'type_guide': (
            '3단 접이 리플릿 기준으로 앞면·뒷면 각 3패널, 총 6패널을 모두 작성하세요.\n'
            '각 패널은 Canva·미리캔버스 등 디자인 툴에 바로 붙여 쓸 수 있도록\n'
            '헤드라인, 본문, 서브카피를 명확히 구분해 작성하세요.\n\n'
            '## [앞면 — 3패널]\n'
            '- [앞 1패널 · 표지(오른쪽)]: 브랜드명 + 핵심 캐치프레이즈 + 서브 메시지\n'
            '- [앞 2패널 · 메인(중간)]: 주요 상품·서비스 소개 + 핵심 혜택 3가지\n'
            '- [앞 3패널 · 서브(왼쪽)]: 브랜드 스토리 또는 사용 방법·후기 요약\n\n'
            '## [뒷면 — 3패널]\n'
            '- [뒤 4패널 · 왼쪽]: 상세 특징 / 성분·소재 / 인증·수상 현황\n'
            '- [뒤 5패널 · 중간]: 가격 안내 / 이벤트·혜택 정보\n'
            '- [뒤 6패널 · 뒷표지(오른쪽)]: CTA 문구 + 연락처 + SNS + QR코드 안내'
        ),
    },
    'flyer': {
        'label':    '전단지',
        'icon':     'bi-file-earmark-image',
        'color':    '#fd7e14',
        'desc':     '단면 홍보 카피 — 오프라인 배포·SNS 홍보물 제작용',
        'target_placeholder': '예: 동네 주민, 행사 방문객',
        'extra_placeholder':  '예: 할인 정보, QR 유도 문구, 유효 기간 등',
        'type_guide': (
            '단면 전단지용 카피를 작성하세요. Canva·미리캔버스에 바로 붙여 쓸 수 있도록\n'
            '헤드라인·본문·CTA를 명확히 구분해 작성하세요.\n\n'
            '## 구성 (권장):\n'
            '헤드라인(캐치프레이즈) / 핵심 혜택 3가지 / 상품 소개 한 줄 / CTA 문구 / 연락처·QR 안내'
        ),
    },
}


# ─────────────────────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────────────────────

@create_bp.route('/printout')
@login_required
def printout():
    from flask import current_app
    supabase = current_app.supabase
    brands   = get_accessible_brands(supabase) if supabase else []
    return render_template(
        'create/printout.html',
        brands=brands,
        printout_types=PRINTOUT_TYPES,
        point_costs={k: POINT_COSTS.get(k, 0) for k in PRINTOUT_TYPES},
        catalog_page_costs=CATALOG_PAGE_COSTS,
    )


@create_bp.route('/printout/generate', methods=['POST'])
@login_required
def printout_generate():
    data          = request.json or {}
    printout_type = (data.get('printout_type') or '').strip()

    if printout_type not in PRINTOUT_TYPES:
        return jsonify(ok=False, message='올바른 인쇄물 유형을 선택해주세요.')

    meta = PRINTOUT_TYPES[printout_type]

    # ── 카탈로그: 분량 선택 처리
    if printout_type == 'catalog':
        try:
            pages = int(data.get('catalog_pages') or 8)
        except (ValueError, TypeError):
            pages = 8
        if pages not in CATALOG_PAGE_COSTS:
            pages = 8

        page_meta  = CATALOG_PAGE_COSTS[pages]
        type_guide = catalog_type_guide(pages)
        point_cost = page_meta['cost']
        max_tokens = page_meta['max_tokens']
        ledger_note = f'카탈로그 ({pages}p)'
        extra_input = {'catalog_pages': pages}

    else:
        type_guide  = meta.get('type_guide', '')
        point_cost  = None   # POINT_COSTS 기본값 사용
        max_tokens  = 3000 if printout_type == 'flyer' else 4000
        ledger_note = None
        extra_input = None

    return run_promo_generation(
        doc_type=printout_type,
        type_meta=meta,
        type_guide=type_guide,
        max_tokens=max_tokens,
        point_cost=point_cost,
        extra_input=extra_input,
        ledger_note_override=ledger_note,
    )
