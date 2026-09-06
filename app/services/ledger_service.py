"""
The general ledger: a real, auto-posted double-entry journal that backs
the entire common Accounting module (Receivables, Payables, Revenue,
Expenses, Cash/Bank, P&L, Dashboard).

Every buyer sale, buyer payment, customer milk entry, customer payment,
late-payment interest charge/waiver, and manual expense/income entry
posts exactly one Debit/Credit pair here (see the post_* functions at the
bottom). The Buyer, Customer, Milk Entries, Payments, and Expenses
modules each make ONE call into this module at their existing
create/edit/delete points - they don't contain any accounting logic
themselves, which is what keeps them "focused on their respective
business operations" while the Accounting module stays common and
centralized.

Chart of accounts and normal balances (standard accounting convention -
asset/expense accounts have a natural DEBIT balance, liability/revenue
accounts have a natural CREDIT balance):

    Accounts Receivable (Asset)   - buyers who owe the dairy money
    Cash/Bank           (Asset)   - money actually on hand
    Milk Purchase Expense (Expense) - cost of milk bought from customers
    Other Expense        (Expense) - manual business expenses
    Accounts Payable    (Liability) - amount owed to customers
    Milk Sales Revenue   (Revenue)  - milk sold to buyers
    Interest Income      (Revenue)  - late-payment interest charged to buyers
    Other Income         (Revenue)  - manual income entries + customer deductions

Every transaction below is a straight textbook entry:

    Buyer sale            : Dr Accounts Receivable / Cr Milk Sales Revenue
    Buyer payment received: Dr Cash/Bank            / Cr Accounts Receivable
    Interest charged       : Dr Accounts Receivable / Cr Interest Income
    Interest waived         : Dr Interest Income     / Cr Accounts Receivable
    Milk purchased          : Dr Milk Purchase Expense / Cr Accounts Payable
    Payment made to customer: Dr Accounts Payable    / Cr Cash/Bank
    Deduction applied        : Dr Accounts Payable    / Cr Milk Purchase Expense (net cost reduction)
    Manual expense            : Dr Other Expense       / Cr Cash/Bank
    Manual income              : Dr Cash/Bank          / Cr Other Income

Idempotency: every posting is keyed by (source_collection, source_id).
Posting again for the same key first deletes whatever was there, then
inserts the fresh pair - so editing a sale/milk entry/deduction/expense
just updates the ledger instead of duplicating it, and deleting one
(source_id, amount=0) leaves nothing behind. Events that can legitimately
recur under the same natural id (a payment cycle paid in several
installments, an interest charge waived more than once) are given a
fresh, uniquely-suffixed source_id per call so each real event keeps its
own permanent pair rather than overwriting the last one.
"""
from datetime import datetime, timezone

from app.utils.id_generator import get_next_sequence

# --- Chart of accounts -------------------------------------------------

ACCOUNTS_RECEIVABLE = "Accounts Receivable"
ACCOUNTS_PAYABLE = "Accounts Payable"
MILK_SALES_REVENUE = "Milk Sales Revenue"
MILK_PURCHASE_EXPENSE = "Milk Purchase Expense"
CASH_BANK = "Cash/Bank"
INTEREST_INCOME = "Interest Income"
OTHER_INCOME = "Other Income"
OTHER_EXPENSE = "Other Expense"

# Accounts whose natural/normal balance is a DEBIT balance (assets & expenses).
# Everything else (liabilities & revenue) has a natural CREDIT balance.
DEBIT_NORMAL_ACCOUNTS = {ACCOUNTS_RECEIVABLE, CASH_BANK, MILK_PURCHASE_EXPENSE, OTHER_EXPENSE}

LABELS = {
    "buyer": {
        "title": "Receivables & Revenue",
        "debit_label": "Revenue",
        "credit_label": "Received",
        "balance_label": "Outstanding Receivable",
    },
    "customer": {
        "title": "Payables & Expenses",
        "debit_label": "Expense",
        "credit_label": "Paid",
        "balance_label": "Outstanding Payable",
    },
}


# ---------------------------------------------------------------------------
# Core posting primitives
# ---------------------------------------------------------------------------


def void_transaction(db, source_collection, source_id):
    """Removes every ledger entry tied back to one source record."""
    db.ledger_entries.delete_many(
        {"source_collection": source_collection, "source_id": str(source_id)}
    )


def post_transaction(
    db,
    *,
    source_collection,
    source_id,
    transaction_type,
    date,
    debit_account,
    credit_account,
    amount,
    entity_type=None,
    entity_id=None,
    entity_name=None,
    description="",
):
    """
    Posts one Debit/Credit journal pair, replacing any existing entries
    for the same (source_collection, source_id) first. Passing an amount
    of 0/None voids the existing entries and posts nothing new (used when
    e.g. a deduction is reset to 0).
    """
    source_id = str(source_id)
    void_transaction(db, source_collection, source_id)

    if not amount or round(amount, 2) <= 0:
        return None

    transaction_id = f"TXN{get_next_sequence(db, 'ledger_transaction_id'):06d}"
    now = datetime.now(timezone.utc)
    common = {
        "transaction_id": transaction_id,
        "date": date,
        "transaction_type": transaction_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "description": description,
        "source_collection": source_collection,
        "source_id": source_id,
        "created_at": now,
    }
    db.ledger_entries.insert_many(
        [
            {
                **common,
                "entry_id": f"LED{get_next_sequence(db, 'ledger_entry_id'):07d}",
                "account": debit_account,
                "side": "Debit",
                "amount": round(amount, 2),
            },
            {
                **common,
                "entry_id": f"LED{get_next_sequence(db, 'ledger_entry_id'):07d}",
                "account": credit_account,
                "side": "Credit",
                "amount": round(amount, 2),
            },
        ]
    )
    return transaction_id


# ---------------------------------------------------------------------------
# Query primitives
# ---------------------------------------------------------------------------


def date_match(start, end):
    """Builds a {"date": {...}} Mongo match clause for an optional [start, end] window."""
    if not (start or end):
        return {}
    date_filter = {}
    if start:
        date_filter["$gte"] = start
    if end:
        date_filter["$lte"] = end
    return {"date": date_filter}


def account_totals(db, account, start=None, end=None, entity_type=None, entity_id=None):
    """Returns (debit_total, credit_total) for one account, optionally scoped."""
    match = {"account": account}
    match.update(date_match(start, end))
    if entity_type:
        match["entity_type"] = entity_type
    if entity_id:
        match["entity_id"] = entity_id
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$side", "total": {"$sum": "$amount"}}},
    ]
    rows = {r["_id"]: r["total"] for r in db.ledger_entries.aggregate(pipeline)}
    return round(rows.get("Debit", 0.0), 2), round(rows.get("Credit", 0.0), 2)


def account_balance(db, account, start=None, end=None, entity_type=None, entity_id=None):
    """
    Net balance for one account, respecting its normal side - always
    returned so that a positive number means "as expected" (money owed
    to the dairy, money the dairy owes, cash on hand, revenue earned...).
    """
    debit, credit = account_totals(db, account, start, end, entity_type, entity_id)
    if account in DEBIT_NORMAL_ACCOUNTS:
        return round(debit - credit, 2)
    return round(credit - debit, 2)


def get_entity_ledger(db, entity_type, entity_id):
    """
    The full Receivable (buyer) or Payable (customer) statement for one
    entity: running balance, entry-by-entry, newest first - reads
    directly and only from ledger_entries, so it's always in sync with
    every other Accounting view.
    """
    account = ACCOUNTS_RECEIVABLE if entity_type == "buyer" else ACCOUNTS_PAYABLE
    rows = list(
        db.ledger_entries.find({"account": account, "entity_id": entity_id}).sort(
            [("date", 1), ("created_at", 1)]
        )
    )

    balance = 0.0
    total_debit = 0.0
    total_credit = 0.0
    entries = []
    for r in rows:
        debit = r["amount"] if r["side"] == "Debit" else 0.0
        credit = r["amount"] if r["side"] == "Credit" else 0.0
        balance = round(balance + debit - credit, 2)
        total_debit = round(total_debit + debit, 2)
        total_credit = round(total_credit + credit, 2)
        entries.append(
            {
                "date": r["date"],
                "label": r["transaction_type"],
                "description": r["description"],
                "debit": debit,
                "credit": credit,
                "balance": balance,
                "ref": r.get("source_id"),
            }
        )

    overdue_count = overdue_amount = None
    if entity_type == "buyer":
        overdue_count = 0
        overdue_amount = 0.0
        for inv in db.buyer_invoices.find({"buyer_id": entity_id, "status": "Overdue"}):
            overdue_count += 1
            overdue_amount = round(overdue_amount + inv.get("remaining_amount", 0.0), 2)

    return {
        "entity_type": entity_type,
        "labels": LABELS[entity_type],
        "summary": {
            "total_debit": total_debit,
            "total_credit": total_credit,
            "outstanding_balance": balance,
            "overdue_count": overdue_count,
            "overdue_amount": overdue_amount,
        },
        "entries": list(reversed(entries)),
    }


def receivables_list(db):
    """One row per buyer with a nonzero Accounts Receivable balance."""
    pipeline = [
        {"$match": {"account": ACCOUNTS_RECEIVABLE}},
        {
            "$group": {
                "_id": "$entity_id",
                "entity_name": {"$last": "$entity_name"},
                "debit": {"$sum": {"$cond": [{"$eq": ["$side", "Debit"]}, "$amount", 0]}},
                "credit": {"$sum": {"$cond": [{"$eq": ["$side", "Credit"]}, "$amount", 0]}},
                "last_activity": {"$max": "$date"},
            }
        },
    ]
    rows = []
    for r in db.ledger_entries.aggregate(pipeline):
        balance = round(r["debit"] - r["credit"], 2)
        if balance == 0:
            continue
        buyer_id = r["_id"]
        open_invoices = list(
            db.buyer_invoices.find({"buyer_id": buyer_id, "remaining_amount": {"$gt": 0}}).sort(
                "due_date", 1
            )
        )
        rows.append(
            {
                "entity_id": buyer_id,
                "entity_name": r["entity_name"],
                "balance": balance,
                "last_activity": r["last_activity"],
                "next_due_date": open_invoices[0]["due_date"] if open_invoices else None,
                "overdue_amount": round(
                    sum(i["remaining_amount"] for i in open_invoices if i["status"] == "Overdue"), 2
                ),
                "overdue_count": sum(1 for i in open_invoices if i["status"] == "Overdue"),
            }
        )
    rows.sort(key=lambda x: -x["balance"])
    return rows


def payables_list(db):
    """One row per customer with a nonzero Accounts Payable balance."""
    pipeline = [
        {"$match": {"account": ACCOUNTS_PAYABLE}},
        {
            "$group": {
                "_id": "$entity_id",
                "entity_name": {"$last": "$entity_name"},
                "debit": {"$sum": {"$cond": [{"$eq": ["$side", "Debit"]}, "$amount", 0]}},
                "credit": {"$sum": {"$cond": [{"$eq": ["$side", "Credit"]}, "$amount", 0]}},
                "last_activity": {"$max": "$date"},
            }
        },
    ]
    rows = []
    for r in db.ledger_entries.aggregate(pipeline):
        balance = round(r["credit"] - r["debit"], 2)
        if balance == 0:
            continue
        customer_id = r["_id"]
        pending_cycles = list(
            db.payments.find({"customer_id": customer_id, "remaining_amount": {"$gt": 0}}).sort(
                "cycle_end", 1
            )
        )
        rows.append(
            {
                "entity_id": customer_id,
                "entity_name": r["entity_name"],
                "balance": balance,
                "last_activity": r["last_activity"],
                # Customer payments don't have a fixed due date like buyer
                # invoices - the end of the oldest still-pending 10-day
                # cycle is the closest equivalent.
                "next_due_date": pending_cycles[0]["cycle_end"] if pending_cycles else None,
                "pending_cycles": len(pending_cycles),
            }
        )
    rows.sort(key=lambda x: -x["balance"])
    return rows


# ---------------------------------------------------------------------------
# Transaction-specific posting helpers - one per real-world event.
# These are the only functions the Buyer/Customer/Milk Entries/Payments/
# Expenses route & service modules ever need to call.
# ---------------------------------------------------------------------------


def post_buyer_sale(db, sale):
    return post_transaction(
        db,
        source_collection="buyer_sales",
        source_id=sale["sale_id"],
        transaction_type="Sale",
        date=sale["sale_date"],
        debit_account=ACCOUNTS_RECEIVABLE,
        credit_account=MILK_SALES_REVENUE,
        amount=sale["total_amount"],
        entity_type="buyer",
        entity_id=sale["buyer_id"],
        entity_name=sale["buyer_name"],
        description=(
            f"{sale['milk_type']} milk sale \u2013 {sale['quantity']:.2f} L "
            f"@ \u20b9{sale['rate_per_litre']:.2f}/L"
        ),
    )


def void_buyer_sale(db, sale_id):
    void_transaction(db, "buyer_sales", sale_id)


def post_buyer_payment(db, payment):
    method = payment.get("payment_method") or "Payment"
    ref = payment.get("transaction_reference")
    ref_text = f" ({ref})" if ref else ""
    description = f"{method}{ref_text} \u2013 against invoice {payment.get('invoice_id', '')}"
    return post_transaction(
        db,
        source_collection="buyer_payments",
        source_id=payment["payment_id"],
        transaction_type="Payment Received",
        date=payment["payment_date"],
        debit_account=CASH_BANK,
        credit_account=ACCOUNTS_RECEIVABLE,
        amount=payment["amount"],
        entity_type="buyer",
        entity_id=payment["buyer_id"],
        entity_name=payment["buyer_name"],
        description=description,
    )


def post_interest_charge(db, charge):
    return post_transaction(
        db,
        source_collection="interest_charges",
        source_id=charge["charge_id"],
        transaction_type="Interest Charged",
        date=charge["calculation_date"],
        debit_account=ACCOUNTS_RECEIVABLE,
        credit_account=INTEREST_INCOME,
        amount=charge["interest_amount"],
        entity_type="buyer",
        entity_id=charge["buyer_id"],
        entity_name=charge["buyer_name"],
        description=(
            f"Interest ({charge.get('days_basis', 0)} day(s) @ "
            f"{charge.get('annual_rate_percent', 0)}% p.a.) on invoice {charge.get('invoice_id', '')}"
        ),
    )


def post_interest_waiver(db, charge, waive_amount, note):
    seq = get_next_sequence(db, "ledger_interest_waiver_id")
    return post_transaction(
        db,
        source_collection="interest_charges",
        source_id=f"{charge['charge_id']}-waive-{seq}",
        transaction_type="Interest Waived",
        date=datetime.now(timezone.utc),
        debit_account=INTEREST_INCOME,
        credit_account=ACCOUNTS_RECEIVABLE,
        amount=waive_amount,
        entity_type="buyer",
        entity_id=charge["buyer_id"],
        entity_name=charge["buyer_name"],
        description=(
            f"Waiver on invoice {charge.get('invoice_id', '')}" + (f" \u2013 {note}" if note else "")
        ),
    )


def post_milk_purchase(db, entry, entry_id):
    return post_transaction(
        db,
        source_collection="milk_entries",
        source_id=entry_id,
        transaction_type="Milk Purchase",
        date=entry["date"],
        debit_account=MILK_PURCHASE_EXPENSE,
        credit_account=ACCOUNTS_PAYABLE,
        amount=entry["total_amount"],
        entity_type="customer",
        entity_id=entry["customer_id"],
        entity_name=entry["customer_name"],
        description=(
            f"{entry['shift']} \u2013 {entry['milk_type']} milk \u2013 {entry['quantity']:.2f} L "
            f"@ \u20b9{entry['rate_per_litre']:.2f}/L"
        ),
    )


def post_customer_payment(db, payment, amount, payment_date, method, reference):
    seq = get_next_sequence(db, "ledger_customer_payment_id")
    ref_text = f" ({reference})" if reference else ""
    description = f"{method or 'Payment'}{ref_text} \u2013 for cycle {payment.get('payment_period', '')}"
    return post_transaction(
        db,
        source_collection="payments",
        source_id=f"{payment['payment_id']}-pay-{seq}",
        transaction_type="Payment Made",
        date=payment_date,
        debit_account=ACCOUNTS_PAYABLE,
        credit_account=CASH_BANK,
        amount=amount,
        entity_type="customer",
        entity_id=payment["customer_id"],
        entity_name=payment["customer_name"],
        description=description,
    )


def post_customer_deduction(db, payment, deductions):
    # Credited against Milk Purchase Expense (not Other Income): a quality
    # deduction is a reduction in what the dairy pays for the milk, so it
    # should net against the cost of that milk, not show up as unrelated
    # business income.
    return post_transaction(
        db,
        source_collection="payments",
        source_id=payment["payment_id"],
        transaction_type="Deduction",
        date=datetime.now(timezone.utc),
        debit_account=ACCOUNTS_PAYABLE,
        credit_account=MILK_PURCHASE_EXPENSE,
        amount=deductions,
        entity_type="customer",
        entity_id=payment["customer_id"],
        entity_name=payment["customer_name"],
        description=f"Deduction applied for cycle {payment.get('payment_period', '')}",
    )


def post_expense_entry(db, entry):
    if entry["entry_type"] == "Income":
        return post_transaction(
            db,
            source_collection="expenses",
            source_id=entry["expense_id"],
            transaction_type="Other Income",
            date=entry["date"],
            debit_account=CASH_BANK,
            credit_account=OTHER_INCOME,
            amount=entry["amount"],
            description=entry.get("description", ""),
        )
    return post_transaction(
        db,
        source_collection="expenses",
        source_id=entry["expense_id"],
        transaction_type="Expense",
        date=entry["date"],
        debit_account=OTHER_EXPENSE,
        credit_account=CASH_BANK,
        amount=entry["amount"],
        description=f"{entry.get('category', '')} \u2013 {entry.get('description', '')}",
    )


def void_expense_entry(db, expense_id):
    void_transaction(db, "expenses", expense_id)
