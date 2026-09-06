from flask import Blueprint, render_template

from app.utils.decorators import login_required

about_bp = Blueprint("about", __name__, url_prefix="/about")


@about_bp.route("/")
@login_required
def index():
    return render_template("about.html")
