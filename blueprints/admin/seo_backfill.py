"""어드민 - Rank Math SEO 메타 백필 (기존 발행글 일괄 설정)"""
import logging
from flask import (request, redirect, url_for, flash, current_app,
                   render_template_string)
from flask_login import login_required
from blueprints.admin import admin_bp
from models import require_superadmin
from services.wordpress_publish import backfill_rankmath_meta

logger = logging.getLogger(__name__)

_PAGE = """
<div style="max-width:680px;margin:48px auto;font-family:system-ui,sans-serif;line-height:1.6">
  <h2>Rank Math SEO 메타 백필</h2>
  <p>기존 발행 글에 <b>초점키워드 · SEO제목 · 메타설명</b>을 일괄 설정합니다.
     (초점키워드 = 제목 주제부 앞 2~3단어)</p>
  <p style="color:#b45309">※ 먼저 WP에 Rank Math 메타 REST 등록 스니펫이 있어야 반영됩니다.</p>
  {% for c in conns %}
    <form method="post" style="margin:14px 0">
      <input type="hidden" name="brand_id" value="{{ c.brand_id }}">
      <button type="submit"
        style="padding:10px 16px;background:#2563eb;color:#fff;border:0;border-radius:8px;cursor:pointer">
        {{ c.site_url }} 백필 실행
      </button>
    </form>
  {% else %}
    <p>워드프레스가 연결된 브랜드가 없습니다.</p>
  {% endfor %}
</div>
"""


@admin_bp.route('/seo/backfill', methods=['GET', 'POST'])
@login_required
@require_superadmin
def seo_backfill():
    sb = current_app.supabase
    if request.method == 'POST':
        brand_id = (request.form.get('brand_id') or '').strip()
        if brand_id:
            r = backfill_rankmath_meta(sb, brand_id)
            flash(
                f"백필 완료 — 총 {r.get('total', 0)}개 · "
                f"성공 {r.get('updated', 0)} · 실패 {r.get('failed', 0)}",
                'success' if r.get('ok') else 'warning')
        return redirect(url_for('admin.seo_backfill'))

    conns = []
    try:
        res = sb.table('wordpress_connections').select('brand_id, site_url').execute()
        conns = res.data or []
    except Exception as e:
        logger.warning('[admin] wp 연결 조회 실패: %s', e)
    return render_template_string(_PAGE, conns=conns)
