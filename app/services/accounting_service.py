"""
Business performance calculations for the Accounting Dashboard: revenue,
milk purchase cost, gross/net profit, expenses, and cash flow.

Two important design decisions carried through this whole module:

1. Profit/Loss is ACCRUAL-based (recognized when the sale/purchase
   happens), while Cash Flow is CASH-based (recognized when money
   actually moves). A buyer paying an invoice is a cash event, not new
   revenue - the revenue was already booked when the sale was recorded.
   Likewise paying a customer for milk already collected is a cash
   event, not a new expense - the cost was already booked when the milk
   entry was recorded. See revenue_breakdown()/milk_purchase_cost() vs
   cash_received()/cash_paid_to_customers() below.

2. "Cash Paid to Customers" is necessarily an approximation: the
   existing customer `payments` collection (kept as-is, unchanged, so
   the existing Customer Milk Collection & Payment module keeps working
   exactly as before) stores only the MOST RECENT payment_date/amount
   per payment record rather than a full per-transaction ledger. For a
   dairy that pays each 10-day cycle in a single transaction (the
   common case) this is exact; if a cycle is paid in several
   installments, the reported cash-paid date range reflects only the
   latest installment. A full transaction ledger (like buyer_payments
   has for buyers) could be added later without touching this file's
   public functions.
"""
from datetime import datetime, timedelta, timezone, date as date_cls


# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------


def _day_bounds(d):
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1, microseconds=-1)
    return start, end


def resolve_range(range_type, custom_from=None, custom_to=None):
    """
    Returns (start, end, label) as UTC datetimes (inclusive) for a named
    range: 'today', 'this_week', 'this_month', or 'custom'.
    """
    today = date_cls.today()

    if range_type == "this_week":
        week_start = today - timedelta(days=today.weekday())
        start, _ = _day_bounds(week_start)
        _, end = _day_bounds(today)
        label = f"{week_start.strftime('%d %b')} \u2013 {today.strftime('%d %b %Y')} (this week)"
    elif range_type == "this_month":
        month_start = today.replace(day=1)
        start, _ = _day_bounds(month_start)
        _, end = _day_bounds(today)
        label = today.strftime("%B %Y")
    elif range_type == "custom" and custom_from and custom_to:
        start, _ = _day_bounds(custom_from)
        _, end = _day_bounds(custom_to)
        label = f"{custom_from.strftime('%d %b %Y')} \u2013 {custom_to.strftime('%d %b %Y')}"
    else:
        range_type = "today"
        start, end = _day_bounds(today)
        label = f"{today.strftime('%d %b %Y')} (today)"

    return start, end, label, range_type


# ---------------------------------------------------------------------------
# Aggregation helper
# ---------------------------------------------------------------------------


def _sum(collection, match, field):
    pipeline = [{"$match": match}, {"$group": {"_id": None, "total": {"$sum": f"${field}"}}}]
    result = list(collection.aggregate(pipeline))
    return round(result[0]["total"], 2) if result else 0.0


# ---------------------------------------------------------------------------
# Accrual-basis figures (Profit & Loss)
# ---------------------------------------------------------------------------


def revenue_breakdown(db, start, end):
    """Revenue = milk sold to buyers (pre-tax) + other income."""
    milk_sales = _sum(db.buyer_sales, {"sale_date": {"$gte": start, "$lte": end}}, "gross_amount")
    other_income = _sum(
        db.expenses, {"date": {"$gte": start, "$lte": end}, "entry_type": "Income"}, "amount"
    )
    return milk_sales, other_income


def milk_purchase_cost(db, start, end):
    """Amount payable to customers for milk collected, at the price recorded on each entry."""
    return _sum(db.milk_entries, {"date": {"$gte": start, "$lte": end}}, "total_amount")


def business_expenses_total(db, start, end):
    """Sum of Expense-type entries only (Other Income is excluded here)."""
    return _sum(
        db.expenses,
        {"date": {"$gte": start, "$lte": end}, "entry_type": {"$ne": "Income"}},
        "amount",
    )


def expenses_by_category(db, start, end):
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lte": end}, "entry_type": {"$ne": "Income"}}},
        {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
        {"$sort": {"total": -1}},
    ]
    return [{"category": r["_id"], "total": round(r["total"], 2)} for r in db.expenses.aggregate(pipeline)]


def interest_charges_net(db, start, end):
    """
    Net late-payment interest actually recognized in this period: interest
    applied during the period, minus any waivers issued during the period
    (even if the original charge happened earlier).
    """
    applied = _sum(db.interest_charges, {"calculation_date": {"$gte": start, "$lte": end}}, "interest_amount")
    waived = _sum(
        db.interest_charges,
        {"waived_at": {"$gte": start, "$lte": end}, "waived_amount": {"$gt": 0}},
        "waived_amount",
    )
    return round(applied - waived, 2)


# ---------------------------------------------------------------------------
# Cash-basis figures (Cash Flow)
# ---------------------------------------------------------------------------


def cash_received(db, start, end):
    """Actual money received: buyer invoice payments + other income."""
    from_buyers = _sum(db.buyer_payments, {"payment_date": {"$gte": start, "$lte": end}}, "amount")
    other_income = _sum(
        db.expenses, {"date": {"$gte": start, "$lte": end}, "entry_type": "Income"}, "amount"
    )
    return from_buyers, other_income


def cash_paid_to_customers(db, start, end):
    """
    Actual money paid out to customers for milk (see the approximation
    note in the module docstring). Filters out unpaid records via
    amount_paid > 0.
    """
    return _sum(
        db.payments,
        {"payment_date": {"$gte": start, "$lte": end}, "amount_paid": {"$gt": 0}},
        "amount_paid",
    )


def cash_paid_expenses(db, start, end):
    return business_expenses_total(db, start, end)


# ---------------------------------------------------------------------------
# Point-in-time snapshots (not range-dependent)
# ---------------------------------------------------------------------------


def outstanding_receivables(db):
    """Total buyers currently owe the dairy, right now, across all invoices."""
    return _sum(db.buyer_invoices, {"remaining_amount": {"$gt": 0}}, "remaining_amount")


def outstanding_payables(db):
    """Total the dairy currently owes customers, right now."""
    return _sum(db.payments, {"remaining_amount": {"$gt": 0}}, "remaining_amount")


def overdue_summary(db):
    cursor = db.buyer_invoices.find({"status": "Overdue"})
    count = 0
    amount = 0.0
    interest_outstanding = 0.0
    for inv in cursor:
        count += 1
        amount += inv.get("remaining_amount", 0.0)
        interest_outstanding += round(inv.get("interest_charged", 0.0) - inv.get("interest_waived", 0.0), 2)
    return {"count": count, "amount": round(amount, 2), "interest": round(interest_outstanding, 2)}


# ---------------------------------------------------------------------------
# Full dashboard payload
# ---------------------------------------------------------------------------


def build_dashboard(db, range_type="today", custom_from=None, custom_to=None):
    start, end, label, resolved_range_type = resolve_range(range_type, custom_from, custom_to)

    sales_revenue, other_income = revenue_breakdown(db, start, end)
    revenue = round(sales_revenue + other_income, 2)
    milk_cost = milk_purchase_cost(db, start, end)
    gross_profit = round(revenue - milk_cost, 2)

    other_expenses = business_expenses_total(db, start, end)
    interest_charges = interest_charges_net(db, start, end)
    net_profit = round(gross_profit - other_expenses - interest_charges, 2)

    from_buyers, other_income_cash = cash_received(db, start, end)
    received = round(from_buyers + other_income_cash, 2)
    paid_customers = cash_paid_to_customers(db, start, end)
    paid_expenses = cash_paid_expenses(db, start, end)
    cash_paid_total = round(paid_customers + paid_expenses, 2)
    net_cash_flow = round(received - cash_paid_total, 2)

    return {
        "range_type": resolved_range_type,
        "range_label": label,
        "start": start,
        "end": end,
        # Profit & Loss (accrual)
        "sales_revenue": sales_revenue,
        "other_income": other_income,
        "revenue": revenue,
        "milk_purchase_cost": milk_cost,
        "gross_profit": gross_profit,
        "other_expenses": other_expenses,
        "interest_charges": interest_charges,
        "net_profit": net_profit,
        "expense_by_category": expenses_by_category(db, start, end),
        # Cash Flow (cash)
        "cash_from_buyers": from_buyers,
        "cash_other_income": other_income_cash,
        "cash_received": received,
        "cash_paid_customers": paid_customers,
        "cash_paid_expenses": paid_expenses,
        "cash_paid_total": cash_paid_total,
        "net_cash_flow": net_cash_flow,
        # Snapshots (as of right now, independent of the selected range)
        "outstanding_receivables": outstanding_receivables(db),
        "outstanding_payables": outstanding_payables(db),
        "overdue": overdue_summary(db),
    }
