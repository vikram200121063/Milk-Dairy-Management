from datetime import datetime, timezone, timedelta, date as date_cls

from flask import Blueprint, render_template, session, current_app

from app.utils.decorators import login_required
from app.services import accounting_service, ledger_service

dashboard_bp = Blueprint("dashboard", __name__)


def _day_bounds(d):
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1, microseconds=-1)
    return start, end


def _last_n_days(n):
    today = date_cls.today()
    return [today - timedelta(days=i) for i in range(n - 1, -1, -1)]


def _trend_series(db):
    """
    Last 7 days of (a) Milk Collection quantity and (b) Revenue vs
    Expense, one point per day, for the two dashboard charts.
    """
    days = _last_n_days(7)
    labels = [d.strftime("%d %b") for d in days]
    milk_quantity = []
    revenue = []
    expense = []

    for d in days:
        start, end = _day_bounds(d)
        day_entries = list(db.milk_entries.find({"date": start}))
        milk_quantity.append(round(sum(e["quantity"] for e in day_entries), 2))

        _, day_revenue = ledger_service.account_totals(db, ledger_service.MILK_SALES_REVENUE, start, end)
        day_milk_cost = ledger_service.account_balance(db, ledger_service.MILK_PURCHASE_EXPENSE, start, end)
        day_other_expense = ledger_service.account_balance(db, ledger_service.OTHER_EXPENSE, start, end)
        revenue.append(round(day_revenue, 2))
        expense.append(round(day_milk_cost + day_other_expense, 2))

    return {
        "labels": labels,
        "milk_quantity": milk_quantity,
        "revenue": revenue,
        "expense": expense,
    }


def _recent_activity(db, limit=8):
    """
    A single, merged, chronological feed across the four kinds of
    day-to-day activity: milk entries, buyer sales, customer payments,
    and buyer payments - so the dashboard tells one story instead of
    two disconnected tables.
    """
    items = []

    for e in db.milk_entries.find().sort("created_at", -1).limit(limit):
        items.append({
            "ts": e.get("created_at") or e["date"],
            "kind": "milk_entry",
            "icon": "milk",
            "title": f"Milk collected \u2013 {e['customer_name']}",
            "detail": f"{e['shift']} \u00b7 {e['quantity']:.1f} L \u00b7 {e['milk_type']}",
            "amount": e["total_amount"],
            "amount_sign": "-",
        })

    for s in db.buyer_sales.find().sort("created_at", -1).limit(limit):
        items.append({
            "ts": s.get("created_at") or s["sale_date"],
            "kind": "buyer_sale",
            "icon": "sale",
            "title": f"Milk sold \u2013 {s['buyer_name']}",
            "detail": f"{s['quantity']:.1f} L \u00b7 {s['milk_type']}",
            "amount": s["total_amount"],
            "amount_sign": "+",
        })

    for p in db.payments.find({"amount_paid": {"$gt": 0}}).sort("updated_at", -1).limit(limit):
        items.append({
            "ts": p.get("updated_at") or p.get("payment_date"),
            "kind": "customer_payment",
            "icon": "payout",
            "title": f"Paid to {p['customer_name']}",
            "detail": f"Cycle {p.get('payment_period', '')} \u00b7 {p.get('payment_method') or 'Payment'}",
            "amount": p["amount_paid"],
            "amount_sign": "-",
        })

    for bp in db.buyer_payments.find().sort("created_at", -1).limit(limit):
        items.append({
            "ts": bp.get("created_at") or bp["payment_date"],
            "kind": "buyer_payment",
            "icon": "receipt",
            "title": f"Received from {bp['buyer_name']}",
            "detail": f"{bp.get('payment_method') or 'Payment'} \u00b7 Invoice {bp.get('invoice_id', '')}",
            "amount": bp["amount"],
            "amount_sign": "+",
        })

    items.sort(key=lambda x: x["ts"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items[:limit]


def _pct_change(today_value, yesterday_value):
    if not yesterday_value:
        return None
    return round(((today_value - yesterday_value) / yesterday_value) * 100, 1)


@dashboard_bp.route("/dashboard")
@login_required
def index():
    db = current_app.db
    today = date_cls.today()
    today_start, today_end = _day_bounds(today)

    # --- Today's Milk Collection ---
    today_entries = list(db.milk_entries.find({"date": today_start}))
    today_milk_quantity = round(sum(e["quantity"] for e in today_entries), 2)
    today_milk_count = len(today_entries)

    # --- Today's Revenue (Milk Sales Revenue booked today, ledger-sourced) ---
    _, today_revenue = ledger_service.account_totals(
        db, ledger_service.MILK_SALES_REVENUE, today_start, today_end
    )
    today_revenue = round(today_revenue, 2)

    # --- Accounts Receivable / Payable (all-time ledger balances) ---
    receivable_balance = accounting_service.outstanding_receivables(db)
    payable_balance = accounting_service.outstanding_payables(db)

    # --- Financial Overview: this month, ledger-sourced ---
    financials = accounting_service.build_dashboard(db, "this_month")

    # --- Charts: last 7 days (also gives us a same-vs-yesterday trend) ---
    trends = _trend_series(db)
    milk_trend_pct = _pct_change(trends["milk_quantity"][-1], trends["milk_quantity"][-2])
    revenue_trend_pct = _pct_change(trends["revenue"][-1], trends["revenue"][-2])

    # --- Recent activity feed ---
    activity = _recent_activity(db)

    # --- Small supporting counts for the stat card sub-lines ---
    total_customers = db.customers.count_documents({})
    total_buyers = db.buyers.count_documents({"status": "Active"})
    overdue = financials["overdue"]
    pending_customer_payments = accounting_service.pending_customer_payments_summary(db)

    # A "Staff" login (see @owner_required) never sees money figures on the
    # dashboard - the template already hides the revenue/finance cards and
    # the Recent Activity feed for them, but that alone isn't enough: the
    # chart data below gets embedded straight into the page's HTML as JSON
    # for Chart.js to read, and unlike a template block a script tag's
    # contents are visible in "View Source" even when nothing renders it
    # on screen. So for Staff we strip the money-carrying series (revenue,
    # expense) out of `trends` here, server-side, before it ever reaches
    # the template - not just hide them with a template conditional.
    is_owner = session.get("role", "Owner") == "Owner"
    if not is_owner:
        trends = {"labels": trends["labels"], "milk_quantity": trends["milk_quantity"]}
        today_revenue = receivable_balance = payable_balance = None
        financials = None
        activity = []

    return render_template(
        "dashboard.html",
        username=session.get("username"),
        today_label=today.strftime("%A, %d %B %Y"),
        today_milk_quantity=today_milk_quantity,
        today_milk_count=today_milk_count,
        today_revenue=today_revenue,
        milk_trend_pct=milk_trend_pct,
        revenue_trend_pct=revenue_trend_pct,
        receivable_balance=receivable_balance,
        payable_balance=payable_balance,
        financials=financials,
        trends=trends,
        activity=activity,
        total_customers=total_customers,
        total_buyers=total_buyers,
        overdue=overdue,
        pending_customer_payments=pending_customer_payments,
    )
