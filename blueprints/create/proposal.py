"""홍보 자료 생성 — 제안서 / 카탈로그 / 리플릿 / 전단지.

라우트:
  GET  /create/proposal            폼 페이지
  POST /create/proposal/generate   생성 요청 (AJAX)
"""
from __future__ import annotations

import logging

from flask import request, jsonify, render_template
from flask_login import login_required, current_user

from blueprints.create import create_bp
from blueprints.create._base import get_accessible_brands, get_brand_by_id, run_text_generation
from models import POINT_COSTS

logger = logging.getLogger(__name__)


# ── 유형별 메타 ───────────────────────────────────────────────
PROPOSAL_TYPES = {
    'business_proposal': {
        'label':    '거래처 제안서',
        'icon':     'bi-briefcase',
        'color':    '#0d6efd',
        'desc':     '납품·공급·협력 등 비즈니스 파트너에게 보내는 정식 제안서',
        'target_placeholder': '예: ○○유통 구매팀',
        'extra_placeholder':  '예: 납품 조건, MOQ, 희망 납품가 등 포함할 내용',
    },
    'sponsorship_proposal': {
        'label':    '협찬 제안서',
        'icon':     'bi-stars',
        'color':    '#d63384',
        'desc':     '인플루언서·행사·미디어 등에 보내는 협찬 제안서',
        'target_placeholder': '예: ○○ 인플루언서 / ○○ 행사 운영사',
        'extra_placeholder':  '예: 협찬 규모, 노출 채널, 기대 효과 등',
    },
    'catalog': {
        'label':    '카탈로그',
        'icon':     'bi-journal-richtext',
        'color':    '#198754',
        'desc':     '상품 라인업을 정리한 제품 카탈로그 (바이어·전시용)',
        'target_placeholder': '예: 도매 바이어, 수출 파트너',
        'extra_placeholder':  '예: 포함할 상품 라인, 특장점, 인증 현황 등',
    },
    'leaflet': {
        'label':    '리플릿',
        'icon':     'bi-newspaper',
        'color':    '#6f42c1',
        'desc':     '행사·박람회·매장용 양면 리플릿 텍스트',
        'target_placeholder': '예: 박람회 방문객, 매장 고객',
        'extra_placeholder':  '예: 행사명, 날짜, 특가·혜택 내용 등',
    },
    'flyer': {
        'label':    '전단지',
        'icon':     'bi-file-earmark-image',
        'color':    '#fd7e14',
        'desc':     '오프라인 배포용 단면 전단지 카피',
        'target_placeholder': '예: 동네 주민, 행사 방문객',
        'extra_placeholder':  '예: 할인 정보, QR 유도 문구 등',
    },
}


def _build_system(proposal_type: str, brand: dict) -> str:
    meta = PROPOSAL_TYPES.get(proposal_type, {})
    label = meta.get('label', '홍보 자료')
    brand_name = brand.get('name', '브랜드')
    return (
        f'당신은 {brand_name} 브랜드의 전문 마케터입니다. '
        f'고객의 브랜드·상품 정보를 바탕으로 실무에서 바로 사용할 수 있는 '
        f'완성도 높은 {label}를 작성합니다. '
        '한국어로 작성하되 전문적이고 신뢰감 있는 문체를 유지하세요. '
        '마크다운 형식(## 제목, - 항목 등)을 활용해 읽기 좋게 구성하세요.'
    )


def _build_user_prompt(proposal_type: str, brand: dict, product: dict | None,
                       target: str, key_points: str, extra: str) -> str:
    meta = PROPOSAL_TYPES.get(proposal_type, {})
    label = meta.get('label', '홍보 자료')

    brand_block = (
        f"브랜드명: {brand.get('name', '')}\n"
        f"업종/카테고리: {brand.get('industry', '')}\n"
        f"타겟 고객: {brand.get('target_customer', '')}\n"
        f"브랜드 소개: {brand.get('extra_context', '')}"
    )

    product_block = ''
    if product:
        feats = '\n'.join(f'  - {f}' for f in (product.get('features') or []))
        product_block = (
            f"\n\n[상품 정보]\n"
            f"상품명: {product.get('name', '')}\n"
            f"카테고리: {product.get('category', '')}\n"
            f"가격: {product.get('price', '')}원\n"
            f"상품 설명: {product.get('description', '')}\n"
            f"주요 특징:\n{feats}"
        )

    target_block = f'\n\n[제안 대상]\n{target}' if target else ''
    points_block = f'\n\n[핵심 내용 / 제안 포인트]\n{key_points}' if key_points else ''
    extra_block  = f'\n\n[추가 요청 사항]\n{extra}' if extra else ''

    type_guide = {
        'business_proposal': (
            '## 목차 구성 (권장):\n'
            '1. 회사/브랜드 소개\n2. 제안 목적\n3. 주요 상품·서비스\n'
            '4. 공급 조건 및 강점\n5. 기대 효과\n6. 협력 제안 내용\n7. 문의처'
        ),
        'sponsorship_proposal': (
            '## 목차 구성 (권장):\n'
            '1. 브랜드 소개\n2. 협찬 목적\n3. 협찬 제안 내용 (제공 물품/금액)\n'
            '4. 기대 노출 및 홍보 효과\n5. 협찬 조건 및 일정\n6. 문의처'
        ),
        'catalog': (
            '## 구성 (권장):\n'
            '1. 브랜드 스토리\n2. 상품 라인업 소개\n3. 주요 상품 상세\n'
            '4. 품질·인증 현황\n5. 주문·납품 안내\n6. 연락처'
        ),
        'leaflet': (
            '## 구성 (권장):\n'
            '[앞면] 헤드라인 / 핵심 메시지 / 이미지 설명\n'
            '[뒷면] 상품 소개 / 혜택 정리 / 행동 유도(CTA) / 연락처'
        ),
        'flyer': (
            '## 구성 (권장):\n'
            '헤드라인(캐치프레이즈) / 핵심 혜택 3가지 / 상품 소개 한 줄 / CTA 문구 / 연락처·QR 안내'
        ),
    }.get(proposal_type, '')

    return (
        f'다음 정보를 바탕으로 {label}를 작성해주세요.\n\n'
        f'[브랜드 정보]\n{brand_block}'
        f'{product_block}'
        f'{target_block}'
        f'{points_block}'
        f'{extra_block}'
        f'\n\n{type_guide}'
    )


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
    from flask import current_app
    supabase = current_app.supabase
    data = request.json or {}

    proposal_type = (data.get('proposal_type') or '').strip()
    brand_id      = (data.get('brand_id')      or '').strip() or None
    product_id    = (data.get('product_id')    or '').strip() or None
    target        = (data.get('target')        or '').strip()
    key_points    = (data.get('key_points')    or '').strip()
    extra         = (data.get('extra')         or '').strip()

    if proposal_type not in PROPOSAL_TYPES:
        return jsonify(ok=False, message='올바른 제안서 유형을 선택해주세요.')
    if not key_points:
        return jsonify(ok=False, message='핵심 내용을 입력해주세요.')

    brand = get_brand_by_id(supabase, brand_id) if brand_id else None
    if not brand:
        from blueprints.create._base import get_default_brand
        brand = get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 정보가 없습니다. 먼저 브랜드를 등록해주세요.')

    product = None
    if product_id:
        try:
            res = supabase.table('products').select('*').eq('id', product_id).execute()
            product = res.data[0] if res.data else None
        except Exception:
            pass

    meta = PROPOSAL_TYPES[proposal_type]
    system_prompt = _build_system(proposal_type, brand)
    user_prompt   = _build_user_prompt(proposal_type, brand, product,
                                       target, key_points, extra)

    # max_tokens: 카탈로그/거래처 제안서는 길게
    max_tokens = 6000 if proposal_type in ('catalog', 'business_proposal') else 4000

    result = run_text_generation(
        creation_type=proposal_type,
        brand=brand,
        input_data={
            'proposal_type': proposal_type,
            'target':        target,
            'key_points':    key_points,
            'extra':         extra,
        },
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        ledger_note=f'{meta["label"]}',
        max_tokens=max_tokens,
    )
    return jsonify(result)
