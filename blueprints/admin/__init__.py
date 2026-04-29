"""어드민 Blueprint"""
from flask import Blueprint

admin_bp = Blueprint('admin', __name__, template_folder='../../templates/admin')

from blueprints.admin import dashboard_views, users_views, settings_views, operator_views  # noqa
