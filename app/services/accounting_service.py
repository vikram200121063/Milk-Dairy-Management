"""
The common, centralized Accounting module: Accounts Receivable, Accounts
Payable, Revenue, Expenses/Costs, Cash/Bank, Profit & Loss, and the
Accounting Dashboard. Every figure here is read from `ledger_entries`
(see app/services/ledger_service.py) - the general ledger that the Buyer,
Customer, Milk Entries, Payments, and Expenses modules auto-post to - so
every view in this module is always in sync with every other one.

Two design decisions carried through this whole module:

1. Profit/Loss is ACCRUAL-based (recognized when the sale/purchase
   happens: a buyer Sale or a customer Milk Purchase), while Cash Flow is
   CASH-based (recognized when money actually moves: a buyer payment or a
   payout to a customer). A buyer settling an invoice is a cash event,
   not new revenue - the revenue was already booked when the sale was
   posted. Likewise paying a customer for milk already collected is a
   cash event, not a new expense.

2. Late-payment interest charged to a buyer is REVENUE (Interest Income)
   the moment it's applied, and a waiver reduces that revenue - both flow
   into Net Profit as income, the same as any other revenue line.
"""
from datetime import datetime, timedelta, timezone, date as date_cls

from app.services import ledger_service as ledger

# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------


def _day_bounds(d):
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1, microseconds=-1)
    return start, end


def resolve_range(range_type, custom_from=None, custom_to=None):
    """
    Returns (start, end, label, range_type) as UTC datetimes (inclusive)
    for a named range: 'today', 'this_week', 'this_month', or 'custom'.
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
# Accrual-basis figures (Profit & Loss) - all sourced from the ledger
# ---------------------------------------------------------------------------


def revenue_breakdown(db, start, end):
    """Revenue = Milk Sales Revenue (buyers) + Other Income (manual entries), for the period."""
    _, milk_sales = ledger.account_totals(db, ledger.MILK_SALES_REVENUE, start, end)
    _, other_income = ledger.account_totals(db, ledger.OTHER_INCOME, start, end)
    return round(milk_sales, 2), round(other_income, 2)


def milk_purchase_cost(db, start, end):
    """Cost of milk bought from customers, net of any quality deductions applied in the period."""
    return ledger.account_balance(db, ledger.MILK_PURCHASE_EXPENSE, start, end)


def business_expenses_total(db, start, end):
    """Manual business expenses recorded in the period (Other Income is not included here)."""
    return ledger.account_balance(db, ledger.OTHER_EXPENSE, start, end)


def expenses_by_category(db, start, end):
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lte": end}, "entry_type": {"$ne": "Income"}}},
        {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
        {"$sort": {"total": -1}},
    ]
    return [{"category": r["_id"], "total": round(r["total"], 2)} for r in db.expenses.aggregate(pipeline)]


def interest_income_net(db, start, end):
    """
    Net interest revenue recognized in the period: interest charged minus
    any waivers issued during the period (even if the original charge
    happened earlier). This is genuine revenue for the dairy, not a cost.
    """
    return ledger.account_balance(db, ledger.INTEREST_INCOME, start, end)


# ---------------------------------------------------------------------------
# Cash-basis figures (Cash Flow) - split from the shared Cash/Bank account
# by which kind of event moved the money
# ---------------------------------------------------------------------------


def _cash_side_by_type(db, side, transaction_type, start, end):
    match = {"account": ledger.CASH_BANK, "side": side, "transaction_type": transaction_type}
    match.update(ledger.date_match(start, end))
    pipeline = [{"$match": match}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]
    result = list(db.ledger_entries.aggregate(pipeline))
    return round(result[0]["total"], 2) if result else 0.0


def cash_received(db, start, end):
    """Actual money received in the period: from buyers + manual income entries."""
    from_buyers = _cash_side_by_type(db, "Debit", "Payment Received", start, end)
    other_income = _cash_side_by_type(db, "Debit", "Other Income", start, end)
    return from_buyers, other_income


def cash_paid_to_customers(db, start, end):
    """Actual money paid out to customers in the period."""
    return _cash_side_by_type(db, "Credit", "Payment Made", start, end)


def cash_paid_expenses(db, start, end):
    """Actual money paid out for manual business expenses in the period."""
    return _cash_side_by_type(db, "Credit", "Expense", start, end)


# ---------------------------------------------------------------------------
# Point-in-time snapshots (not range-dependent - all-time ledger balances)
# ---------------------------------------------------------------------------


def outstanding_receivables(db):
    """Total buyers currently owe the dairy, right now (Accounts Receivable balance)."""
    return ledger.account_balance(db, ledger.ACCOUNTS_RECEIVABLE)


def outstanding_payables(db):
    """Total the dairy currently owes customers, right now (Accounts Payable balance)."""
    return ledger.account_balance(db, ledger.ACCOUNTS_PAYABLE)


def cash_bank_balance(db):
    """Money on hand right now: everything received minus everything paid out, all-time."""
    return ledger.account_balance(db, ledger.CASH_BANK)


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


def pending_customer_payments_summary(db):
    """Mirror of overdue_summary() for the payable side: customer cycles still owed money."""
    cursor = db.payments.find({"remaining_amount": {"$gt": 0}})
    count = 0
    amount = 0.0
    for p in cursor:
        count += 1
        amount += p.get("remaining_amount", 0.0)
    return {"count": count, "amount": round(amount, 2)}


# ---------------------------------------------------------------------------
# Full dashboard payload
# ---------------------------------------------------------------------------


def build_dashboard(db, range_type="today", custom_from=None, custom_to=None):
    start, end, label, resolved_range_type = resolve_range(range_type, custom_from, custom_to)

    sales_revenue, other_income = revenue_breakdown(db, start, end)
    milk_cost = milk_purchase_cost(db, start, end)
    interest_income = interest_income_net(db, start, end)

    revenue = round(sales_revenue + other_income, 2)
    gross_profit = round(revenue - milk_cost, 2)

    other_expenses = business_expenses_total(db, start, end)
    # Interest income is genuine revenue, so it ADDS to net profit (it used
    # to be incorrectly subtracted before this module read from the ledger).
    net_profit = round(gross_profit - other_expenses + interest_income, 2)

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
        "interest_income": interest_income,
        "revenue": revenue,
        "milk_purchase_cost": milk_cost,
        "gross_profit": gross_profit,
        "other_expenses": other_expenses,
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
        "cash_bank_balance": cash_bank_balance(db),
        "overdue": overdue_summary(db),
        "pending_customer_payments": pending_customer_payments_summary(db),
    }


def monthly_trend(db, months=6):
    """
    Revenue vs Expense for each of the last `months` calendar months
    (oldest first), for the Accounting Dashboard's trend chart. Revenue =
    Milk Sales Revenue + Interest Income; Expense = Milk Purchase Cost
    (net of deductions) + Other Expenses - same definitions used
    everywhere else in this module.
    """
    today = date_cls.today()
    points = []
    for i in range(months - 1, -1, -1):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(microseconds=1)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(microseconds=1)

        sales_revenue, other_income = revenue_breakdown(db, start, end)
        interest = interest_income_net(db, start, end)
        revenue = round(sales_revenue + other_income + interest, 2)
        expense = round(milk_purchase_cost(db, start, end) + business_expenses_total(db, start, end), 2)

        points.append({"label": start.strftime("%b %Y"), "revenue": revenue, "expense": expense})
    return points


# ---------------------------------------------------------------------------
# Revenue report - #3 in the Accounting requirements
# ---------------------------------------------------------------------------


def revenue_report(db, start=None, end=None):
    _, milk_credit = ledger.account_totals(db, ledger.MILK_SALES_REVENUE, start, end)
    _, int_credit = ledger.account_totals(db, ledger.INTEREST_INCOME, start, end)
    total_revenue = round(milk_credit + int_credit, 2)

    match = {"account": ledger.MILK_SALES_REVENUE, "side": "Credit"}
    match.update(ledger.date_match(start, end))

    by_buyer_pipeline = [
        {"$match": match},
        {"$group": {"_id": "$entity_id", "buyer_name": {"$last": "$entity_name"}, "total": {"$sum": "$amount"}}},
        {"$sort": {"total": -1}},
    ]
    by_buyer = [
        {"buyer_id": r["_id"], "buyer_name": r["buyer_name"], "total": round(r["total"], 2)}
        for r in db.ledger_entries.aggregate(by_buyer_pipeline)
    ]

    by_month_pipeline = [
        {"$match": match},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m", "date": "$date"}}, "total": {"$sum": "$amount"}}},
        {"$sort": {"_id": 1}},
    ]
    by_month = [
        {"month": r["_id"], "total": round(r["total"], 2)} for r in db.ledger_entries.aggregate(by_month_pipeline)
    ]

    return {
        "milk_sales_revenue": round(milk_credit, 2),
        "interest_income": round(int_credit, 2),
        "total_revenue": total_revenue,
        "by_buyer": by_buyer,
        "by_month": by_month,
    }


# ---------------------------------------------------------------------------
# Expense / cost report - #4 in the Accounting requirements
# ---------------------------------------------------------------------------


def expense_report(db, start=None, end=None):
    milk_cost = ledger.account_balance(db, ledger.MILK_PURCHASE_EXPENSE, start, end)
    other_expense = ledger.account_balance(db, ledger.OTHER_EXPENSE, start, end)

    match = {"account": ledger.MILK_PURCHASE_EXPENSE, "side": "Debit"}
    match.update(ledger.date_match(start, end))
    by_customer_pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": "$entity_id",
                "customer_name": {"$last": "$entity_name"},
                "total": {"$sum": "$amount"},
            }
        },
        {"$sort": {"total": -1}},
    ]
    by_customer = [
        {"customer_id": r["_id"], "customer_name": r["customer_name"], "total": round(r["total"], 2)}
        for r in db.ledger_entries.aggregate(by_customer_pipeline)
    ]

    categories = expenses_by_category(
        db, start or datetime.min.replace(tzinfo=timezone.utc), end or datetime.now(timezone.utc)
    )

    return {
        "milk_purchase_cost": milk_cost,
        "other_expense": other_expense,
        "total_cost": round(milk_cost + other_expense, 2),
        "by_customer": by_customer,
        "expense_by_category": categories,
    }


# ---------------------------------------------------------------------------
# Cash / Bank - #5 in the Accounting requirements
# ---------------------------------------------------------------------------


def cash_bank_summary(db, start=None, end=None):
    debit, credit = ledger.account_totals(db, ledger.CASH_BANK, start, end)
    return {
        "received": debit,
        "paid": credit,
        "net_for_range": round(debit - credit, 2),
        "balance": cash_bank_balance(db),
    }


def _as_aware_utc(dt):
    """
    pymongo (and mongomock) return naive datetimes by default (interpreted
    as UTC) even though this app always WRITES timezone-aware UTC
    datetimes. Every other function in this module compares dates inside
    a MongoDB query filter, where that mismatch never surfaces - this is
    the one place that compares already-fetched Python datetimes directly,
    so it needs to normalize both sides first.
    """
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def cash_bank_transactions(db, start=None, end=None, limit=300):
    """
    Every Cash/Bank movement with a running all-time balance, newest
    first. The running balance is computed over the FULL history (so it's
    always accurate) and only the requested date window is returned.
    """
    start, end = _as_aware_utc(start), _as_aware_utc(end)
    rows = list(db.ledger_entries.find({"account": ledger.CASH_BANK}).sort([("date", 1), ("created_at", 1)]))
    balance = 0.0
    annotated = []
    for r in rows:
        delta = r["amount"] if r["side"] == "Debit" else -r["amount"]
        balance = round(balance + delta, 2)
        row_date = _as_aware_utc(r["date"])
        in_range = (not start or row_date >= start) and (not end or row_date <= end)
        if in_range:
            annotated.append(
                {
                    "date": r["date"],
                    "transaction_type": r["transaction_type"],
                    "description": r["description"],
                    "entity_name": r.get("entity_name"),
                    "direction": "In" if r["side"] == "Debit" else "Out",
                    "amount": r["amount"],
                    "balance": balance,
                }
            )
    return list(reversed(annotated))[:limit]


# ---------------------------------------------------------------------------
# Profit & Loss statement - #6 in the Accounting requirements
# ---------------------------------------------------------------------------


def profit_and_loss(db, start, end):
    sales_revenue, other_income = revenue_breakdown(db, start, end)
    interest_income = interest_income_net(db, start, end)
    total_revenue = round(sales_revenue + other_income + interest_income, 2)

    milk_cost = milk_purchase_cost(db, start, end)
    gross_profit = round(total_revenue - milk_cost, 2)

    other_expenses = business_expenses_total(db, start, end)
    net_profit = round(gross_profit - other_expenses, 2)

    return {
        "sales_revenue": sales_revenue,
        "other_income": other_income,
        "interest_income": interest_income,
        "total_revenue": total_revenue,
        "milk_purchase_cost": milk_cost,
        "gross_profit": gross_profit,
        "other_expenses": other_expenses,
        "expense_by_category": expenses_by_category(db, start, end),
        "net_profit": net_profit,
    }
