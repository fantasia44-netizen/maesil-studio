"""홍보 자료(제안서 / 인쇄물) 공통 헬퍼."""
from __future__ import annotations

from flask import current_app, request, jsonify
from flask_login import current_user

from blueprints.create._base import (
    get_accessible_brands, get_brand_by_id, get_default_brand,
    run_text_generation,
)
from models import POINT_COSTS


def _brand_block(brand: dict) -> str:
    return (
        f"브랜드명: {brand.get('name', '')}\n"
        f"업종/카테고리: {brand.get('industry', '')}\n"
        f"타겟 고객: {brand.get('target_customer', '')}\n"
        f"브랜드 소개: {brand.get('extra_context', '')}"
    )


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

    result = run_text_generation(
        creation_type=doc_type,
        brand=brand,
        input_data=input_data,
        system_prompt=build_system(label, brand),
        user_prompt=build_user_prompt(label, type_guide, brand, product,
                                      target, key_points, extra),
        point_cost=point_cost,
        ledger_note=ledger_note_override or label,
        max_tokens=max_tokens,
    )
    return jsonify(result)
