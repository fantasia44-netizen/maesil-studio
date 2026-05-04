"""홍보물 생성 — 제안서 + 인쇄물 통합.

라우트:
  GET  /create/promo            폼 페이지
  POST /create/promo/generate   생성 요청 (AJAX)
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


# ── 카탈로그 분량별 요금 ──────────────────────────────────────
CATALOG_PAGE_COSTS = {
    8:  {'label': '8p',  'cost': 150, 'max_tokens': 3000},
    16: {'label': '16p', 'cost': 300, 'max_tokens': 5000},
    32: {'label': '32p', 'cost': 500, 'max_tokens': 8000},
}

def _catalog_type_guide(pages: int) -> str:
    if pages == 8:
        return (
            '## 구성 (권장 — 8페이지):\n'
            '- p.1 (표지): 브랜드명 + 핵심 슬로건 + 대표 이미지 설명\n'
            '- p.2 (브랜드 소개): 브랜드 스토리 + 핵심 가치\n'
            '- p.3~4 (상품 소개): 주요 상품 2~3종 상세 소개\n'
            '- p.5 (특장점): 차별화 포인트 + 인증·수상 현황\n'
            '- p.6 (가격·주문): 가격표 + 최소 주문 수량 + 납기\n'
            '- p.7 (연락처): 담당자 정보 + 오시는 길\n'
            '- p.8 (뒤표지): 브랜드 태그라인 + 연락처 요약'
        )
    if pages == 16:
        return (
            '## 구성 (권장 — 16페이지):\n'
            '- p.1 (표지): 브랜드명 + 핵심 슬로건\n'
            '- p.2~3 (브랜드 소개): 스토리 + 비전 + 핵심 가치\n'
            '- p.4~5 (라인업 개요): 전체 상품 라인업 한눈에 보기\n'
            '- p.6~11 (상품 상세): 주요 상품 4~6종 개별 소개\n'
            '- p.12~13 (품질·인증): 제조 공정 + 인증서 + 수상 이력\n'
            '- p.14 (가격·납품 조건): 가격표 + MOQ + 결제 조건\n'
            '- p.15 (CS·연락처): 고객센터 + 담당자 + SNS\n'
            '- p.16 (뒤표지): 브랜드 태그라인 + 연락처 요약'
        )
    return (
        '## 구성 (권장 — 32페이지):\n'
        '- p.1 (표지): 브랜드명 + 슬로건\n'
        '- p.2~5 (브랜드 스토리): 창업 배경 + 철학 + 성장 이력\n'
        '- p.6~7 (라인업 전체): 카테고리별 상품 구성 개요\n'
        '- p.8~25 (상품 상세): 카테고리별 상품 10~15종 개별 소개\n'
        '- p.26~27 (품질·기술): 소재·공정·연구개발 + 인증 현황\n'
        '- p.28~29 (수출·B2B): 해외 진출 현황 / 납품 실적\n'
        '- p.30 (가격·거래 조건): 가격표 + 거래 조건\n'
        '- p.31 (CS·연락처): 고객센터 + 오시는 길\n'
        '- p.32 (뒤표지): 브랜드 태그라인 + 연락처'
    )


# ── 전체 유형 (제안서 + 인쇄물) ──────────────────────────────
ALL_PROMO_TYPES = {
    # ── 제안서
    'business_proposal': {
        'label':    '거래처 제안서',
        'icon':     'bi-briefcase',
        'color':    '#0d6efd',
        'group':    '제안서',
        'desc':     '납품·공급·협력 비즈니스 파트너용 정식 제안서',
        'target_placeholder': '예: ○○유통 구매팀, ○○마트 상품팀',
        'extra_placeholder':  '예: 납품 조건, MOQ, 희망 납품가 등',
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
        'group':    '제안서',
        'desc':     '인플루언서·행사·미디어용 협찬 제안서',
        'target_placeholder': '예: ○○ 인플루언서 / ○○ 행사 운영사',
        'extra_placeholder':  '예: 협찬 규모, 노출 채널, 기대 효과 등',
        'type_guide': (
            '## 목차 구성 (권장):\n'
            '1. 브랜드 소개\n2. 협찬 목적\n3. 협찬 제안 내용 (제공 물품/금액)\n'
            '4. 기대 노출 및 홍보 효과\n5. 협찬 조건 및 일정\n6. 문의처'
        ),
    },
    # ── 인쇄물
    'catalog': {
        'label':    '카탈로그',
        'icon':     'bi-journal-richtext',
        'color':    '#198754',
        'group':    '인쇄물',
        'desc':     '페이지별 카피 초안 — Canva·미리캔버스에 바로 붙여 편집',
        'target_placeholder': '예: 도매 바이어, 수출 파트너, 전시회 방문객',
        'extra_placeholder':  '예: 포함할 상품 라인, 특장점, 인증 현황 등',
        'has_page_select': True,
    },
    'leaflet': {
        'label':    '리플릿',
        'icon':     'bi-newspaper',
        'color':    '#6f42c1',
        'group':    '인쇄물',
        'desc':     '3단 접이 6패널 카피 — 디자인 툴에 패널별로 바로 적용',
        'target_placeholder': '예: 박람회 방문객, 매장 고객',
        'extra_placeholder':  '예: 행사명, 날짜, 특가·혜택 내용 등',
        'type_guide': (
            '3단 접이 리플릿 기준으로 앞면·뒷면 각 3패널, 총 6패널을 모두 작성하세요.\n'
            'Canva·미리캔버스에 바로 붙여 쓸 수 있도록 헤드라인·본문·서브카피를 명확히 구분하세요.\n\n'
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
        'group':    '인쇄물',
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

@create_bp.route('/promo')
@login_required
def promo():
    from flask import current_app
    supabase = current_app.supabase
    brands   = get_accessible_brands(supabase) if supabase else []
    return render_template(
        'create/promo.html',
        brands=brands,
        promo_types=ALL_PROMO_TYPES,
        point_costs={k: POINT_COSTS.get(k, 0) for k in ALL_PROMO_TYPES},
        catalog_page_costs=CATALOG_PAGE_COSTS,
    )


@create_bp.route('/promo/generate', methods=['POST'])
@login_required
def promo_generate():
    data      = request.json or {}
    doc_type  = (data.get('doc_type') or '').strip()

    if doc_type not in ALL_PROMO_TYPES:
        return jsonify(ok=False, message='올바른 유형을 선택해주세요.')

    meta = ALL_PROMO_TYPES[doc_type]

    # 카탈로그 — 분량 선택
    if doc_type == 'catalog':
        try:
            pages = int(data.get('catalog_pages') or 8)
        except (ValueError, TypeError):
            pages = 8
        if pages not in CATALOG_PAGE_COSTS:
            pages = 8
        page_meta   = CATALOG_PAGE_COSTS[pages]
        type_guide  = _catalog_type_guide(pages)
        point_cost  = page_meta['cost']
        max_tokens  = page_meta['max_tokens']
        ledger_note = f'카탈로그 ({pages}p)'
        extra_input = {'catalog_pages': pages}
    else:
        type_guide  = meta.get('type_guide', '')
        point_cost  = None
        max_tokens  = 6000 if doc_type == 'business_proposal' else \
                      4000 if doc_type == 'sponsorship_proposal' else \
                      3000 if doc_type == 'flyer' else 4000
        ledger_note = None
        extra_input = None

    return run_promo_generation(
        doc_type=doc_type,
        type_meta=meta,
        type_guide=type_guide,
        max_tokens=max_tokens,
        point_cost=point_cost,
        extra_input=extra_input,
        ledger_note_override=ledger_note,
    )
