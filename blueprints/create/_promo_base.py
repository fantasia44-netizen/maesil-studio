"""홍보 자료(제안서 / 인쇄물) 공통 헬퍼."""
from __future__ import annotations

from flask import current_app, request, jsonify
from flask_login import current_user, login_required

from blueprints.create import create_bp
from blueprints.create._base import (
    get_accessible_brands, get_brand_by_id, get_default_brand,
)
from models import POINT_COSTS, CREATION_MODELS
from services.async_generation import (
    AsyncSubmitError, submit_async_generation, render_status_response,
)


# ── 카탈로그 분량별 요금 (promo.py / printout.py 공통) ─────────
CATALOG_PAGE_COSTS = {
    8:  {'label': '8p',  'cost': 150, 'max_tokens': 3000},
    16: {'label': '16p', 'cost': 300, 'max_tokens': 5000},
    32: {'label': '32p', 'cost': 500, 'max_tokens': 8000},
}


def catalog_type_guide(pages: int) -> str:
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


def _brand_block(brand: dict) -> str:
    lines = [
        f"브랜드명: {brand.get('name', '')}",
        f"업종/카테고리: {brand.get('industry', '')}",
        f"타겟 고객: {brand.get('target_customer', '')}",
        f"브랜드 소개: {brand.get('extra_context', '')}",
    ]
    # 확장 필드 — 값이 있을 때만 추가
    if brand.get('founded_year'):
        lines.append(f"창업 연도: {brand['founded_year']}년")
    if brand.get('ceo_name'):
        lines.append(f"대표자: {brand['ceo_name']}")
    if brand.get('employee_count'):
        lines.append(f"직원 규모: {brand['employee_count']}")
    if brand.get('address'):
        lines.append(f"주소: {brand['address']}")
    if brand.get('contact_phone'):
        lines.append(f"연락처: {brand['contact_phone']}")
    if brand.get('contact_email'):
        lines.append(f"이메일: {brand['contact_email']}")
    if brand.get('website'):
        lines.append(f"홈페이지: {brand['website']}")
    if brand.get('certifications'):
        lines.append(f"인증·수상 이력: {brand['certifications']}")
    if brand.get('key_stats'):
        lines.append(f"핵심 성과 수치: {brand['key_stats']}")
    if brand.get('references_text'):
        lines.append(f"주요 거래처·납품처: {brand['references_text']}")
    return '\n'.join(lines)


def _product_block(product: dict) -> str:
    if not product:
        return ''
    feats = '\n'.join(f'  - {f}' for f in (product.get('features') or []))
    return (
        f"\n\n[상품 정보]\n"
        f"상품명: {product.get('name', '')}\n"
        f"카테고리: {product.get('category', '')}\n"
        f"가격: {product.get('price', '')}원\n"
        f"상품 설명: {product.get('description', '')}\n"
        f"주요 특징:\n{feats}"
    )


def build_system(label: str, brand: dict) -> str:
    return (
        f"당신은 {brand.get('name', '브랜드')} 브랜드의 전문 마케터입니다. "
        f"고객의 브랜드·상품 정보를 바탕으로 실무에서 바로 사용할 수 있는 "
        f"완성도 높은 {label}를 작성합니다. "
        "한국어로 작성하되 전문적이고 신뢰감 있는 문체를 유지하세요. "
        "마크다운 형식(## 제목, - 항목 등)을 활용해 읽기 좋게 구성하세요."
    )


def build_user_prompt(label: str, type_guide: str, brand: dict,
                      product: dict | None,
                      target: str, key_points: str, extra: str) -> str:
    parts = [
        f'다음 정보를 바탕으로 {label}를 작성해주세요.\n\n'
        f'[브랜드 정보]\n{_brand_block(brand)}',
        _product_block(product),
        f'\n\n[제안/배포 대상]\n{target}'   if target     else '',
        f'\n\n[핵심 내용 / 포인트]\n{key_points}' if key_points else '',
        f'\n\n[추가 요청 사항]\n{extra}'    if extra      else '',
        f'\n\n{type_guide}'                if type_guide else '',
    ]
    return ''.join(parts)


def fetch_product(supabase, product_id: str) -> dict | None:
    if not product_id:
        return None
    try:
        res = supabase.table('products').select('*').eq('id', product_id).execute()
        return res.data[0] if res.data else None
    except Exception:
        return None


def run_promo_generation(doc_type: str, type_meta: dict, type_guide: str,
                         max_tokens: int = 4000,
                         point_cost: int | None = None,
                         extra_input: dict | None = None,
                         ledger_note_override: str | None = None):
    """제안서/인쇄물 공통 생성 플로우.

    Args:
      point_cost: 동적 비용 (None 이면 POINT_COSTS[doc_type] 사용).
      extra_input: input_data 에 추가 저장할 필드 (예: {'catalog_pages': 16}).
      ledger_note_override: 포인트 원장 메모 커스텀 (예: '카탈로그 (16p)').
    """
    supabase = current_app.supabase
    data = request.json or {}

    brand_id   = (data.get('brand_id')   or '').strip() or None
    product_id = (data.get('product_id') or '').strip() or None
    target     = (data.get('target')     or '').strip()
    key_points = (data.get('key_points') or '').strip()
    extra      = (data.get('extra')      or '').strip()

    if not key_points:
        return jsonify(ok=False, message='핵심 내용을 입력해주세요.')

    brand = get_brand_by_id(supabase, brand_id) if brand_id else None
    if not brand:
        brand = get_default_brand(supabase)
    if not brand:
        return jsonify(ok=False, message='브랜드 정보가 없습니다. 먼저 브랜드를 등록해주세요.')

    product = fetch_product(supabase, product_id)
    label   = type_meta.get('label', '문서')

    input_data = {'doc_type': doc_type, 'target': target,
                  'key_points': key_points, 'extra': extra}
    if extra_input:
        input_data.update(extra_input)

    cost = point_cost if point_cost is not None else POINT_COSTS.get(doc_type, 0)
    from services.claude_service import DEFAULT_MODEL
    resolved_model = CREATION_MODELS.get(doc_type, DEFAULT_MODEL)

    system_prompt = build_system(label, brand)
    user_prompt = build_user_prompt(label, type_guide, brand, product,
                                    target, key_points, extra)

    from tasks.promo_task import generate_promo_text

    try:
        creation_id = submit_async_generation(
            owner=current_user,
            creation_type=doc_type,
            cost=cost,
            input_data=input_data,
            task_delay_fn=generate_promo_text.delay,
            task_kwargs={
                'system_prompt': system_prompt,
                'user_prompt': user_prompt,
                'max_tokens': max_tokens,
                'model': resolved_model,
            },
            model_used=resolved_model,
            extra_row={'brand_id': brand['id']} if brand and brand.get('id') else None,
            note_override=ledger_note_override or label,
        )
    except AsyncSubmitError as e:
        return jsonify(ok=False, message=str(e))

    return jsonify(ok=True, id=creation_id, async_mode=True, cost=cost)


@create_bp.route('/promo-doc/status/<cid>', methods=['GET'])
@login_required
def promo_doc_status(cid):
    """제안서/인쇄물 생성 Celery 태스크 완료 여부 폴링 (printout.py/promo.py 공유)."""
    supabase = current_app.supabase
    if not supabase:
        return jsonify(ok=False, status='error', message='DB 연결이 없습니다.')
    try:
        r = supabase.table('creations').select(
            'id, status, output_data, user_id'
        ).eq('id', cid).single().execute()
        row = r.data
    except Exception:
        row = None
    return render_status_response(row, current_user.id, done_fields={'text': 'text'})
