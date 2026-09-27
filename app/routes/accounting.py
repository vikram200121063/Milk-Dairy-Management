from datetime import datetime, date as date_cls

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
from app.services import accounting_service, ai_service, finance_settings_service, ledger_service

accounting_bp = Blueprint("accounting", __name__, url_prefix="/accounting")

RANGE_CHOICES = (
    ("today", "Today"),
    ("this_week", "This Week"),
    ("this_month", "This Month"),
    ("custom", "Custom Range"),
)


def _parse_range(default_range_type="this_month"):
    """
    Shared range picker used by the Revenue, Expense, Cash/Bank, and P&L
    report pages (the Dashboard keeps its own "today"-default call to
    accounting_service.resolve_range() directly). Returns
    (start, end, label, range_type, custom_from_str, custom_to_str).
    """
    range_type = request.args.get("range", default_range_type)
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
            flash("Please provide a valid custom date range. Showing this month instead.", "warning")
            range_type = "this_month"

    start, end, label, resolved_range_type = accounting_service.resolve_range(
        range_type, custom_from, custom_to
    )
    return start, end, label, resolved_range_type, custom_from_str, custom_to_str


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
    trend = accounting_service.monthly_trend(current_app.db, months=6)

    # Profit as a share of revenue, for the gauge arc. Guarded against a
    # zero-revenue range (a brand new dairy, or a day with no sales yet).
    margin = round((data["net_profit"] / data["revenue"] * 100), 1) if data["revenue"] else 0.0

    return render_template(
        "accounting/dashboard.html",
        data=data,
        trend=trend,
        margin=margin,
        range_choices=RANGE_CHOICES,
        range_type=data["range_type"],
        range_label=data["range_label"],
        custom_from=custom_from_str,
        custom_to=custom_to_str,
        today=date_cls.today().isoformat(),
    )


@accounting_bp.route("/ai-summary", methods=["POST"])
@login_required
def ai_summary():
    """
    AJAX endpoint for the "AI Summary" button on the Accounting dashboard.
    Re-resolves whatever range is currently selected there and asks Claude
    to turn those same figures into a short plain-English narrative.
    """
    range_type = request.args.get("range", "today")
    custom_from_str = request.args.get("from", "")
    custom_to_str = request.args.get("to", "")

    custom_from = custom_to = None
    if range_type == "custom":
        try:
            custom_from = datetime.strptime(custom_from_str, "%Y-%m-%d").date()
            custom_to = datetime.strptime(custom_to_str, "%Y-%m-%d").date()
        except ValueError:
            range_type = "today"

    data = accounting_service.build_dashboard(current_app.db, range_type, custom_from, custom_to)
    narrative, error = ai_service.generate_pnl_narrative(
        data, data["range_label"], current_app.config.get("DAIRY_NAME", "the dairy")
    )
    if error:
        return jsonify({"error": error}), 200
    return jsonify({"summary": narrative})


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


# ---------------------------------------------------------------------------
# Accounts Receivable - buyers who owe the dairy money
# ---------------------------------------------------------------------------


@accounting_bp.route("/receivables")
@login_required
def receivables():
    rows = ledger_service.receivables_list(current_app.db)
    total_outstanding = round(sum(r["balance"] for r in rows), 2)
    total_overdue = round(sum(r["overdue_amount"] for r in rows), 2)
    return render_template(
        "accounting/receivables.html",
        rows=rows,
        total_outstanding=total_outstanding,
        total_overdue=total_overdue,
    )


@accounting_bp.route("/receivables/<buyer_id>")
@login_required
def receivable_detail(buyer_id):
    buyer = current_app.db.buyers.find_one({"buyer_id": buyer_id})
    if not buyer:
        flash("Buyer not found.", "danger")
        return redirect(url_for("accounting.receivables"))
    ledger = ledger_service.get_entity_ledger(current_app.db, "buyer", buyer_id)
    return render_template("accounting/receivable_detail.html", buyer=buyer, ledger=ledger)


# ---------------------------------------------------------------------------
# Accounts Payable - customers the dairy owes money to
# ---------------------------------------------------------------------------


@accounting_bp.route("/payables")
@login_required
def payables():
    rows = ledger_service.payables_list(current_app.db)
    total_outstanding = round(sum(r["balance"] for r in rows), 2)
    return render_template("accounting/payables.html", rows=rows, total_outstanding=total_outstanding)


@accounting_bp.route("/payables/<customer_id>")
@login_required
def payable_detail(customer_id):
    customer = current_app.db.customers.find_one({"customer_id": customer_id})
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("accounting.payables"))
    ledger = ledger_service.get_entity_ledger(current_app.db, "customer", customer_id)
    return render_template("accounting/payable_detail.html", customer=customer, ledger=ledger)


# ---------------------------------------------------------------------------
# Revenue report
# ---------------------------------------------------------------------------


@accounting_bp.route("/revenue")
@login_required
def revenue():
    start, end, label, range_type, custom_from, custom_to = _parse_range()
    data = accounting_service.revenue_report(current_app.db, start, end)
    return render_template(
        "accounting/revenue.html",
        data=data,
        range_choices=RANGE_CHOICES,
        range_type=range_type,
        range_label=label,
        custom_from=custom_from,
        custom_to=custom_to,
        today=date_cls.today().isoformat(),
    )


# ---------------------------------------------------------------------------
# Expense / cost report
# ---------------------------------------------------------------------------


@accounting_bp.route("/expense-report")
@login_required
def expense_report():
    start, end, label, range_type, custom_from, custom_to = _parse_range()
    data = accounting_service.expense_report(current_app.db, start, end)
    return render_template(
        "accounting/expense_report.html",
        data=data,
        range_choices=RANGE_CHOICES,
        range_type=range_type,
        range_label=label,
        custom_from=custom_from,
        custom_to=custom_to,
        today=date_cls.today().isoformat(),
    )


# ---------------------------------------------------------------------------
# Cash / Bank
# ---------------------------------------------------------------------------


@accounting_bp.route("/cash-bank")
@login_required
def cash_bank():
    start, end, label, range_type, custom_from, custom_to = _parse_range()
    summary = accounting_service.cash_bank_summary(current_app.db, start, end)
    transactions = accounting_service.cash_bank_transactions(current_app.db, start, end)
    return render_template(
        "accounting/cash_bank.html",
        summary=summary,
        transactions=transactions,
        range_choices=RANGE_CHOICES,
        range_type=range_type,
        range_label=label,
        custom_from=custom_from,
        custom_to=custom_to,
        today=date_cls.today().isoformat(),
    )


# ---------------------------------------------------------------------------
# Profit & Loss statement
# ---------------------------------------------------------------------------


@accounting_bp.route("/pnl")
@login_required
def pnl():
    start, end, label, range_type, custom_from, custom_to = _parse_range()
    data = accounting_service.profit_and_loss(current_app.db, start, end)
    return render_template(
        "accounting/pnl.html",
        data=data,
        range_choices=RANGE_CHOICES,
        range_type=range_type,
        range_label=label,
        custom_from=custom_from,
        custom_to=custom_to,
        today=date_cls.today().isoformat(),
    )
