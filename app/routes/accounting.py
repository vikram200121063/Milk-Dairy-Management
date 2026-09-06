from datetime import datetime, date as date_cls

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)

from app.utils.decorators import login_required
from app.services import accounting_service, finance_settings_service

accounting_bp = Blueprint("accounting", __name__, url_prefix="/accounting")

RANGE_CHOICES = (
    ("today", "Today"),
    ("this_week", "This Week"),
    ("this_month", "This Month"),
    ("custom", "Custom Range"),
)


@accounting_bp.route("/")
@login_required
def dashboard():
    range_type = request.args.get("range", "today")
    custom_from_str = request.args.get("from", "")
    custom_to_str = request.args.get("to", "")

    custom_from = custom_to = None
    if range_type == "custom":
        try:
            custom_from = datetime.strptime(custom_from_str, "%Y-%m-%d").date()
            custom_to = datetime.strptime(custom_to_str, "%Y-%m-%d").date()
            if custom_from > custom_to:
                custom_from, custom_to = custom_to, custom_from
        except ValueError:
            flash("Please provide a valid custom date range. Showing today instead.", "warning")
            range_type = "today"

    data = accounting_service.build_dashboard(current_app.db, range_type, custom_from, custom_to)

    return render_template(
        "accounting/dashboard.html",
        data=data,
        range_choices=RANGE_CHOICES,
        custom_from=custom_from_str,
        custom_to=custom_to_str,
        today=date_cls.today().isoformat(),
    )


@accounting_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        errors = []

        def parse(field, label, cast=float):
            raw = request.form.get(field, "").strip()
            try:
                value = cast(raw)
                if value < 0:
                    errors.append(f"{label} cannot be negative.")
                return value
            except ValueError:
                errors.append(f"{label} must be a number.")
                return None

        default_payment_terms_days = parse("default_payment_terms_days", "Default payment terms", int)
        grace_period_days = parse("grace_period_days", "Grace period", int)
        interest_rate_annual_percent = parse("interest_rate_annual_percent", "Interest rate")
        default_tax_percentage = parse("default_tax_percentage", "Default tax percentage")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("accounting/settings.html", settings=request.form)

        finance_settings_service.update_settings(
            current_app.db,
            default_payment_terms_days=default_payment_terms_days,
            grace_period_days=grace_period_days,
            interest_rate_annual_percent=interest_rate_annual_percent,
            default_tax_percentage=default_tax_percentage,
        )
        flash("Financial settings saved.", "success")
        return redirect(url_for("accounting.settings"))

    current_settings = finance_settings_service.get_settings(current_app.db)
    return render_template("accounting/settings.html", settings=current_settings)
