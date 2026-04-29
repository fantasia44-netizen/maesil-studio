"""랜딩 페이지"""
from flask import Blueprint, render_template
from flask_login import current_user
from flask import redirect, url_for

landing_bp = Blueprint('landing', __name__)


@landing_bp.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('landing/index.html')
