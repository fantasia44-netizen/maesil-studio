"""생성 Blueprint 모음"""
from flask import Blueprint

create_bp = Blueprint('create', __name__, template_folder='../../templates/create')

from blueprints.create import blog, instagram, detail_page, thumbnail, ad_copy, brand_kit, image, shorts  # noqa
