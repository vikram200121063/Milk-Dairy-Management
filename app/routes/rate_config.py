from datetime import datetime, timezone

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
    jsonify,
)

from app.utils.decorators import login_required
from app.services.rate_service import calculate_rate, MILK_TYPES

rate_config_bp = Blueprint("rate_config", __name__, url_prefix="/rate-config")


@rate_config_bp.route("/")
@login_required
def list_configs():
    configs = {
        milk_type: current_app.db.rate_configurations.find_one({"milk_type": milk_type})
        for milk_type in MILK_TYPES
    }
    return render_template("rate_config/list.html", configs=configs)


@rate_config_bp.route("/edit/<milk_type>", methods=["GET", "POST"])
@login_required
def edit_config(milk_type):
    if milk_type not in MILK_TYPES:
        flash("Invalid milk type.", "danger")
        return redirect(url_for("rate_config.list_configs"))

    existing = current_app.db.rate_configurations.find_one({"milk_type": milk_type})

    if request.method == "POST":
        errors = []

        def parse(field, label):
            raw = request.form.get(field, "").strip()
            try:
                return float(raw)
            except ValueError:
                errors.append(f"{label} must be a number.")
                return None

        base_rate = parse("base_rate", "Base rate")
        fat_rate_per_point = parse("fat_rate_per_point", "Fat rate per point")
        snf_rate_per_point = parse("snf_rate_per_point", "SNF rate per point")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "rate_config/form.html", milk_type=milk_type, config=request.form
            )

        current_app.db.rate_configurations.update_one(
            {"milk_type": milk_type},
            {
                "$set": {
                    "milk_type": milk_type,
                    "base_rate": base_rate,
                    "fat_rate_per_point": fat_rate_per_point,
                    "snf_rate_per_point": snf_rate_per_point,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        flash(f"{milk_type} milk rate configuration saved.", "success")
        return redirect(url_for("rate_config.list_configs"))

    return render_template("rate_config/form.html", milk_type=milk_type, config=existing or {})


@rate_config_bp.route("/calculate")
@login_required
def calculate_preview():
    """
    Small JSON endpoint used by the milk entry form's JavaScript to show a
    live preview of the automatic rate as the admin types Fat%/SNF%.
    This is a CONVENIENCE preview only - the milk_entries routes always
    recalculate the authoritative rate themselves on the server side.
    """
    milk_type = request.args.get("milk_type", "")
    try:
        fat = float(request.args.get("fat", ""))
        snf = float(request.args.get("snf", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "Enter valid Fat % and SNF % first."}), 400

    rate, error = calculate_rate(current_app.db, milk_type, fat, snf)
    if error:
        return jsonify({"error": error}), 404
    return jsonify({"rate": rate})
