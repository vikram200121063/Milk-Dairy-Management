"""
Core business logic for consolidated buyer invoices: computing the two
half-month billing periods (1st-15th and 16th-month end), generating
invoices from recorded buyer_sales, and recording payments against them.

Mirrors the structure of app/services/payment_service.py (the customer
10-day payment cycle logic) so the two modules stay easy to compare, but
buyer invoices are intentionally NOT rolled forward into the next
period's invoice the way customer pending balances are - each buyer
invoice stands on its own so dunning/interest can be tracked per-invoice.
"""
import calendar
from datetime import datetime, timezone, timedelta

from app.utils.id_generator import get_next_sequence

PERIOD_1_START_DAY = 1
PERIOD_1_END_DAY = 15


def get_period_boundaries(year, month, period_number):
    """
    Returns (start, end) as UTC midnight datetimes, inclusive, for one of
    the two half-month buyer billing periods:
        Period 1: day 1-15
        Period 2: day 16-last day of month
    """
    if period_number == 1:
        start_day, end_day = PERIOD_1_START_DAY, PERIOD_1_END_DAY
    elif period_number == 2:
        start_day = PERIOD_1_END_DAY + 1
        end_day = calendar.monthrange(year, month)[1]
    else:
        raise ValueError("period_number must be 1 or 2")

    start = datetime(year, month, start_day, tzinfo=timezone.utc)
    end = datetime(year, month, end_day, tzinfo=timezone.utc)
    return start, end


def make_billing_period(year, month, period_number):
    """e.g. (2026, 8, 1) -> '2026-08-B1'"""
    return f"{year:04d}-{month:02d}-B{period_number}"


def current_period_defaults():
    """Sensible (year, month, period_number) for 'what period are we in right now'."""
    today = datetime.now(timezone.utc)
    period = 1 if today.day <= PERIOD_1_END_DAY else 2
    return today.year, today.month, period


def compute_invoice_status(remaining_amount, amount_paid, due_date, today):
    """
    Single source of truth for invoice status, used by generation, payment
    recording, and the dunning cycle so all three always agree:
        Paid            -> balance fully cleared, regardless of date
        Overdue         -> due date has passed and money is still owed
        Partially Paid  -> some (but not all) has been paid, not yet overdue
        Unpaid          -> nothing paid yet, not yet overdue
    """
    if remaining_amount <= 0:
        return "Paid"
    if today.date() > due_date.date():
        return "Overdue"
    if amount_paid > 0:
        return "Partially Paid"
    return "Unpaid"


def generate_invoices_for_period(db, year, month, period_number, settings=None):
    """
    Computes (or recomputes) one consolidated invoice per buyer for every
    buyer_sales record in the given half-month period.

    Safe to call more than once for the same period: existing invoices
    are updated (quantities/amounts recalculated from the underlying
    sales) rather than duplicated. amount_paid and any interest already
    applied are always preserved - regenerating never erases money
    already collected or late fees already charged.

    Returns the number of invoices created or updated.
    """
    from app.services import finance_settings_service

    start, end = get_period_boundaries(year, month, period_number)
    period = make_billing_period(year, month, period_number)
    settings = settings or finance_settings_service.get_settings(db)
    now = datetime.now(timezone.utc)

    pipeline = [
        {"$match": {"sale_date": {"$gte": start, "$lte": end}}},
        {
            "$group": {
                "_id": "$buyer_id",
                "buyer_name": {"$first": "$buyer_name"},
                "total_quantity": {"$sum": "$quantity"},
                "total_entries": {"$sum": 1},
                "gross_amount": {"$sum": "$gross_amount"},
                "tax_amount": {"$sum": "$tax_amount"},
                "other_charges": {"$sum": "$other_charges"},
                "sale_total": {"$sum": "$total_amount"},
            }
        },
    ]
    rows = {row["_id"]: row for row in db.buyer_sales.aggregate(pipeline)}

    updated_count = 0
    for buyer_id, row in rows.items():
        buyer = db.buyers.find_one({"buyer_id": buyer_id})
        buyer_name = row["buyer_name"] or (buyer["company_name"] if buyer else buyer_id)
        payment_terms_days = (
            (buyer or {}).get("payment_terms_days") or settings["default_payment_terms_days"]
        )

        existing = db.buyer_invoices.find_one({"buyer_id": buyer_id, "billing_period": period})

        invoice_date = existing["invoice_date"] if existing else now
        due_date = invoice_date + timedelta(days=payment_terms_days)
        amount_paid = existing["amount_paid"] if existing else 0.0

        # Preserve any late-payment interest already applied/waived on this
        # invoice - regenerating must never wipe out charges already levied.
        interest_charged = existing.get("interest_charged", 0.0) if existing else 0.0
        interest_waived = existing.get("interest_waived", 0.0) if existing else 0.0
        net_interest = round(interest_charged - interest_waived, 2)

        principal_amount = round(row["sale_total"], 2)
        total_amount = round(principal_amount + net_interest, 2)
        remaining = max(round(total_amount - amount_paid, 2), 0)
        status = compute_invoice_status(remaining, amount_paid, due_date, now)

        set_fields = {
            "buyer_id": buyer_id,
            "buyer_name": buyer_name,
            "billing_period": period,
            "period_start": start,
            "period_end": end,
            "invoice_date": invoice_date,
            "due_date": due_date,
            "payment_terms_days": payment_terms_days,
            "total_quantity": round(row["total_quantity"], 2),
            "total_entries": row["total_entries"],
            "gross_amount": round(row["gross_amount"], 2),
            "tax_amount": round(row["tax_amount"], 2),
            "other_charges": round(row["other_charges"], 2),
            "principal_amount": principal_amount,
            "total_amount": total_amount,
            "amount_paid": amount_paid,
            "remaining_amount": remaining,
            "status": status,
            "interest_charged": round(interest_charged, 2),
            "interest_waived": round(interest_waived, 2),
            "updated_at": now,
        }

        if existing:
            # Overdue day count and interest are only ever advanced by the
            # dunning cycle, not by regeneration - just carry them forward.
            set_fields["overdue_days"] = existing.get("overdue_days", 0)
            set_fields["last_interest_calc_date"] = existing.get("last_interest_calc_date")
            set_fields["last_reminder_sent_at"] = existing.get("last_reminder_sent_at")
            db.buyer_invoices.update_one({"_id": existing["_id"]}, {"$set": set_fields})
        else:
            seq = get_next_sequence(db, "buyer_invoice_id")
            set_fields["invoice_id"] = f"BINV{seq:05d}"
            set_fields["overdue_days"] = 0
            set_fields["last_interest_calc_date"] = None
            set_fields["last_reminder_sent_at"] = None
            set_fields["created_at"] = now
            db.buyer_invoices.insert_one(set_fields)

        updated_count += 1

    return updated_count


def record_buyer_payment(db, invoice_id, amount, method, reference, payment_date):
    """
    Records one payment transaction against a buyer invoice as its own
    document in buyer_payments (unlike the customer payments module,
    buyer payments keep a FULL transaction history, one document per
    payment - see buyer_payments collection). Then recomputes the
    invoice's amount_paid/remaining_amount/status from all payments on
    file for it.
    """
    invoice = db.buyer_invoices.find_one({"invoice_id": invoice_id})
    if not invoice:
        return False, "Invoice not found."
    if amount <= 0:
        return False, "Payment amount must be greater than 0."

    now = datetime.now(timezone.utc)
    seq = get_next_sequence(db, "buyer_payment_id")
    payment_id = f"BPAY{seq:05d}"

    db.buyer_payments.insert_one(
        {
            "payment_id": payment_id,
            "invoice_id": invoice_id,
            "buyer_id": invoice["buyer_id"],
            "buyer_name": invoice["buyer_name"],
            "amount": round(amount, 2),
            "payment_date": payment_date,
            "payment_method": method,
            "transaction_reference": reference,
            "created_at": now,
        }
    )

    new_amount_paid = round(invoice["amount_paid"] + amount, 2)
    remaining = max(round(invoice["total_amount"] - new_amount_paid, 2), 0)
    status = compute_invoice_status(remaining, new_amount_paid, invoice["due_date"], now)
    overdue_days = invoice.get("overdue_days", 0) if status == "Overdue" else 0

    db.buyer_invoices.update_one(
        {"_id": invoice["_id"]},
        {
            "$set": {
                "amount_paid": new_amount_paid,
                "remaining_amount": remaining,
                "status": status,
                "overdue_days": overdue_days,
                "updated_at": now,
            }
        },
    )
    return True, payment_id


def payments_for_invoice(db, invoice_id):
    return list(db.buyer_payments.find({"invoice_id": invoice_id}).sort("payment_date", 1))


def sales_for_invoice(db, invoice):
    """
    Buyer sales aren't tagged with an invoice_id (same pattern the
    existing app uses for milk_entries/payments) - the line items for an
    invoice are simply every sale to that buyer within its billing period.
    """
    return list(
        db.buyer_sales.find(
            {
                "buyer_id": invoice["buyer_id"],
                "sale_date": {"$gte": invoice["period_start"], "$lte": invoice["period_end"]},
            }
        ).sort("sale_date", 1)
    )
