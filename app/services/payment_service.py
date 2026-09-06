import calendar
from datetime import datetime, timezone

from app.utils.id_generator import get_next_sequence
from app.services import ledger_service


def get_cycle_boundaries(year, month, cycle_number):
    """
    Returns (start, end) as UTC midnight datetimes, inclusive, for one of
    the dairy's three 10-day payment cycles in a given month:
        Cycle 1: day 1-10
        Cycle 2: day 11-20
        Cycle 3: day 21-last day of month
    """
    if cycle_number == 1:
        start_day, end_day = 1, 10
    elif cycle_number == 2:
        start_day, end_day = 11, 20
    elif cycle_number == 3:
        start_day = 21
        end_day = calendar.monthrange(year, month)[1]
    else:
        raise ValueError("cycle_number must be 1, 2, or 3")

    start = datetime(year, month, start_day, tzinfo=timezone.utc)
    end = datetime(year, month, end_day, tzinfo=timezone.utc)
    return start, end


def make_payment_period(year, month, cycle_number):
    """e.g. (2026, 8, 1) -> '2026-08-C1'"""
    return f"{year:04d}-{month:02d}-C{cycle_number}"


def get_previous_period(year, month, cycle_number):
    """Returns (year, month, cycle_number) for the cycle immediately before this one."""
    if cycle_number > 1:
        return year, month, cycle_number - 1
    if month == 1:
        return year - 1, 12, 3
    return year, month - 1, 3


def _status_for(remaining_amount, amount_paid):
    if remaining_amount <= 0:
        return "Paid"
    if amount_paid > 0:
        return "Partially Paid"
    return "Pending"


def generate_payments_for_cycle(db, year, month, cycle_number):
    """
    Computes (or recomputes) payment records for every customer relevant to
    this cycle: anyone who delivered milk in the cycle's date range, PLUS
    anyone who still has an unpaid balance carried over from the previous
    cycle (even with zero deliveries this cycle - otherwise their pending
    balance would silently vanish).

    Safe to call more than once for the same cycle: existing payment
    records are updated (gross amount, quantity, previous pending balance
    recalculated) rather than duplicated, and any amount already paid is
    preserved.

    Returns the number of payment records created or updated.
    """
    start, end = get_cycle_boundaries(year, month, cycle_number)
    period = make_payment_period(year, month, cycle_number)

    prev_year, prev_month, prev_cycle = get_previous_period(year, month, cycle_number)
    prev_period = make_payment_period(prev_year, prev_month, prev_cycle)

    pipeline = [
        {"$match": {"date": {"$gte": start, "$lte": end}}},
        {
            "$group": {
                "_id": "$customer_id",
                "customer_name": {"$first": "$customer_name"},
                "total_quantity": {"$sum": "$quantity"},
                "total_entries": {"$sum": 1},
                "gross_amount": {"$sum": "$total_amount"},
            }
        },
    ]
    entries_by_customer = {row["_id"]: row for row in db.milk_entries.aggregate(pipeline)}

    prev_pending_cursor = db.payments.find(
        {"payment_period": prev_period, "remaining_amount": {"$gt": 0}}
    )
    pending_by_customer = {p["customer_id"]: p["remaining_amount"] for p in prev_pending_cursor}

    customer_ids = set(entries_by_customer) | set(pending_by_customer)
    updated_count = 0

    for customer_id in customer_ids:
        row = entries_by_customer.get(customer_id)
        if row:
            total_quantity = round(row["total_quantity"], 2)
            total_entries = row["total_entries"]
            gross_amount = round(row["gross_amount"], 2)
            customer_name = row["customer_name"]
        else:
            total_quantity, total_entries, gross_amount = 0, 0, 0
            customer_doc = db.customers.find_one({"customer_id": customer_id})
            customer_name = customer_doc["name"] if customer_doc else customer_id

        previous_pending = round(pending_by_customer.get(customer_id, 0), 2)

        existing = db.payments.find_one({"customer_id": customer_id, "payment_period": period})
        # Preserve anything already recorded against this payment.
        deductions = existing["deductions"] if existing else 0
        amount_paid = existing["amount_paid"] if existing else 0

        final_payable = round(gross_amount - deductions + previous_pending, 2)
        remaining = max(round(final_payable - amount_paid, 2), 0)
        status = _status_for(remaining, amount_paid)

        set_fields = {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "payment_period": period,
            "cycle_start": start,
            "cycle_end": end,
            "total_quantity": total_quantity,
            "total_entries": total_entries,
            "gross_amount": gross_amount,
            "deductions": deductions,
            "previous_pending_amount": previous_pending,
            "final_payable_amount": final_payable,
            "amount_paid": amount_paid,
            "remaining_amount": remaining,
            "payment_status": status,
            "updated_at": datetime.now(timezone.utc),
        }

        if existing:
            db.payments.update_one({"_id": existing["_id"]}, {"$set": set_fields})
        else:
            seq = get_next_sequence(db, "payment_id")
            set_fields["payment_id"] = f"PAY{seq:05d}"
            set_fields["payment_date"] = None
            set_fields["payment_method"] = None
            set_fields["transaction_reference"] = None
            set_fields["created_at"] = datetime.now(timezone.utc)
            db.payments.insert_one(set_fields)

        updated_count += 1

    return updated_count


def update_deductions(db, payment_id, deductions):
    """Updates the deduction amount for a payment and recalculates everything downstream."""
    payment = db.payments.find_one({"payment_id": payment_id})
    if not payment:
        return False, "Payment record not found."

    final_payable = round(
        payment["gross_amount"] - deductions + payment["previous_pending_amount"], 2
    )
    remaining = max(round(final_payable - payment["amount_paid"], 2), 0)
    status = _status_for(remaining, payment["amount_paid"])

    db.payments.update_one(
        {"payment_id": payment_id},
        {
            "$set": {
                "deductions": deductions,
                "final_payable_amount": final_payable,
                "remaining_amount": remaining,
                "payment_status": status,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    # Common Accounting module: re-post Dr Accounts Payable / Cr Other Income
    # for the current deduction amount (0 voids any previous deduction entry).
    ledger_service.post_customer_deduction(db, payment, deductions)
    return True, None


def record_payment(db, payment_id, amount, method, reference, payment_date):
    """
    Records a payment transaction against a payment record. Amounts are
    cumulative - recording ₹500 twice results in amount_paid of ₹1000.
    payment_date/method/reference reflect the MOST RECENT transaction only;
    for full multi-transaction history, extend this to append to a list.
    """
    payment = db.payments.find_one({"payment_id": payment_id})
    if not payment:
        return False, "Payment record not found."
    if amount <= 0:
        return False, "Payment amount must be greater than 0."

    new_amount_paid = round(payment["amount_paid"] + amount, 2)
    remaining = max(round(payment["final_payable_amount"] - new_amount_paid, 2), 0)
    status = _status_for(remaining, new_amount_paid)

    db.payments.update_one(
        {"payment_id": payment_id},
        {
            "$set": {
                "amount_paid": new_amount_paid,
                "remaining_amount": remaining,
                "payment_status": status,
                "payment_date": payment_date,
                "payment_method": method,
                "transaction_reference": reference,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    # Common Accounting module: auto-post Dr Accounts Payable / Cr Cash/Bank
    # for THIS installment only (record_payment is cumulative, so each call
    # is its own real cash event and gets its own permanent ledger pair).
    ledger_service.post_customer_payment(db, payment, amount, payment_date, method, reference)
    return True, None
