"""제안서 생성 — 거래처 제안서 / 협찬 제안서.

라우트:
  GET  /create/proposal            폼 페이지
  POST /create/proposal/generate   생성 요청 (AJAX)
"""
from __future__ import annotations

import logging

from flask import render_template, request, jsonify
from flask_login import login_required

from blueprints.create import create_bp
from blueprints.create._base import get_accessible_brands
from blueprints.create._promo_base import run_promo_generation
from models import POINT_COSTS

logger = logging.getLogger(__name__)


# ── 유형별 메타 ───────────────────────────────────────────────
PROPOSAL_TYPES = {
    'business_proposal': {
        'label':    '거래처 제안서',
        'icon':     'bi-briefcase',
        'color':    '#0d6efd',
        'desc':     '납품·공급·협력 등 비즈니스 파트너에게 보내는 정식 제안서',
        'target_placeholder': '예: ○○유통 구매팀, ○○마트 상품팀',
        'extra_placeholder':  '예: 납품 조건, MOQ, 희망 납품가 등 포함할 내용',
        'type_guide': (
            '## 목차 구성 (권장):\n'
            '1. 회사/브랜드 소개\n2. 제안 목적\n3. 주요 상품·서비스\n'
            '4. 공급 조건 및 강점\n5. 기대 효과\n6. 협력 제안 내용\n7. 문의처'
        ),
    },
    'sponsorship_proposal': {
        'label':    '협찬 제안서',
        'icon':     'bi-stars',
        'color':    '#d63384',
        'desc':     '인플루언서·행사·미디어 등에 보내는 협찬 제안서',
        'target_placeholder': '예: ○○ 인플루언서 / ○○ 행사 운영사',
        'extra_placeholder':  '예: 협찬 규모, 노출 채널, 기대 효과 등',
        'type_guide': (
            '## 목차 구성 (권장):\n'
            '1. 브랜드 소개\n2. 협찬 목적\n3. 협찬 제안 내용 (제공 물품/금액)\n'
            '4. 기대 노출 및 홍보 효과\n5. 협찬 조건 및 일정\n6. 문의처'
        ),
    },
}


# ─────────────────────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────────────────────

@create_bp.route('/proposal')
@login_required
def proposal():
    from flask import current_app
    supabase = current_app.supabase
    brands   = get_accessible_brands(supabase) if supabase else []
    return render_template(
        'create/proposal.html',
        brands=brands,
        proposal_types=PROPOSAL_TYPES,
        point_costs={k: POINT_COSTS.get(k, 0) for k in PROPOSAL_TYPES},
    )


@create_bp.route('/proposal/generate', methods=['POST'])
@login_required
def proposal_generate():
    data          = request.json or {}
    proposal_type = (data.get('proposal_type') or '').strip()

    if proposal_type not in PROPOSAL_TYPES:
        return jsonify(ok=False, message='올바른 제안서 유형을 선택해주세요.')

    meta       = PROPOSAL_TYPES[proposal_type]
    max_tokens = 6000 if proposal_type == 'business_proposal' else 4000

    return run_promo_generation(
        doc_type=proposal_type,
        type_meta=meta,
        type_guide=meta.get('type_guide', ''),
        max_tokens=max_tokens,
    )
