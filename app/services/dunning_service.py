"""
Handles everything that happens to a buyer invoice AFTER its due date has
passed:
    1. Mark it Overdue and compute how many days overdue it is.
    2. Send one polite reminder email (logged to dunning_records).
    3. Wait out a configurable grace period.
    4. Only after the grace period, start accruing late-payment interest
       on the remaining balance (also logged, to interest_charges).

`run_dunning_cycle()` does all four steps for every overdue invoice in
one pass and is meant to be triggered periodically - either by clicking
"Run Dunning Check" in the Buyer Invoices screen, or by wiring up the
`flask process-dunning` CLI command (see app/__init__.py) to a daily
cron job / scheduled task, since this project has no background job
runner of its own.

Interest is charged on the CURRENT remaining balance (which may already
include interest from a previous run) for the number of days since the
last calculation - i.e. it's simple interest recalculated incrementally
each run, not recomputed from scratch, so running this twice in the same
day never double-charges.
"""
from datetime import datetime, timedelta, timezone

from app.services import ai_service, email_service, finance_settings_service, ledger_service
from app.services.buyer_invoice_service import compute_invoice_status
from app.utils.id_generator import get_next_sequence

REMINDER = "Reminder"
INVOICE_ISSUED = "InvoiceIssued"


# ---------------------------------------------------------------------------
# Reminder emails
# ---------------------------------------------------------------------------


def _log_dunning(db, invoice, record_type, status, error, overdue_days, manual):
    db.dunning_records.insert_one(
        {
            "invoice_id": invoice["invoice_id"],
            "buyer_id": invoice["buyer_id"],
            "buyer_name": invoice["buyer_name"],
            "record_type": record_type,
            "channel": "email",
            "manual": manual,
            "overdue_days_at_send": overdue_days,
            "status": status,
            "error": error,
            "sent_at": datetime.now(timezone.utc),
        }
    )


def send_dunning_reminder(db, invoice, dairy_info, overdue_days, manual=False):
    """
    Sends the payment-reminder email for one overdue invoice and logs the
    attempt to dunning_records regardless of outcome, so there's always a
    complete audit trail of what was (or wasn't) sent and why.
    Returns a short status string: "sent", "failed", or "skipped".
    """
    buyer = db.buyers.find_one({"buyer_id": invoice["buyer_id"]})
    if not buyer or not buyer.get("email"):
        status, error = "skipped", "No email on file for this buyer."
    elif not email_service.email_configured():
        status, error = "skipped", "Email not configured."
    else:
        # If AI is configured, tone-adjust the reminder based on how many
        # times this buyer has already been reminded (across all their
        # invoices) - first time gets a warm nudge, repeat offenders get
        # something firmer. draft_dunning_paragraph() returns None on any
        # failure, in which case send_dunning_reminder_email() just falls
        # back to its static paragraph - a reminder always goes out.
        prior_reminder_count = db.dunning_records.count_documents(
            {"buyer_id": invoice["buyer_id"], "record_type": REMINDER, "status": "sent"}
        )
        ai_message = ai_service.draft_dunning_paragraph(buyer, invoice, overdue_days, prior_reminder_count)
        try:
            email_service.send_dunning_reminder_email(
                buyer, invoice, dairy_info, overdue_days, custom_message=ai_message
            )
            status, error = "sent", None
        except email_service.EmailSendError as exc:
            status, error = "failed", str(exc)

    _log_dunning(db, invoice, REMINDER, status, error, overdue_days, manual)
    if status == "sent":
        db.buyer_invoices.update_one(
            {"_id": invoice["_id"]},
            {"$set": {"last_reminder_sent_at": datetime.now(timezone.utc)}},
        )
    return status


def notify_buyer_invoice(db, invoice, dairy_info, manual=True):
    """Sends the 'your consolidated invoice is ready' email for one invoice."""
    buyer = db.buyers.find_one({"buyer_id": invoice["buyer_id"]})
    if not buyer or not buyer.get("email"):
        status, error = "skipped", "No email on file for this buyer."
    elif not email_service.email_configured():
        status, error = "skipped", "Email not configured."
    else:
        try:
            email_service.send_buyer_invoice_email(db, buyer, invoice, dairy_info)
            status, error = "sent", None
        except email_service.EmailSendError as exc:
            status, error = "failed", str(exc)

    _log_dunning(db, invoice, INVOICE_ISSUED, status, error, invoice.get("overdue_days", 0), manual)
    return status


def dunning_history(db, invoice_id):
    return list(db.dunning_records.find({"invoice_id": invoice_id}).sort("sent_at", -1))


# ---------------------------------------------------------------------------
# Interest calculation
# ---------------------------------------------------------------------------


def calculate_and_apply_interest(db, invoice, settings, today):
    """
    Applies one incremental interest charge to `invoice` if (a) its grace
    period has passed and (b) at least one day has elapsed since interest
    was last calculated for it. Mutates `invoice` in place to keep the
    caller's copy in sync, and returns the new interest_charges document,
    or None if no charge was applicable right now.
    """
    if invoice.get("remaining_amount", 0) <= 0:
        return None

    due_date = invoice["due_date"]
    grace_days = int(settings.get("grace_period_days", 7))
    grace_end_date = (due_date + timedelta(days=grace_days)).date()
    if today <= grace_end_date:
        return None

    last_calc = invoice.get("last_interest_calc_date")
    basis_date = last_calc.date() if last_calc else grace_end_date
    days_elapsed = (today - basis_date).days
    if days_elapsed <= 0:
        return None

    annual_rate = float(settings.get("interest_rate_annual_percent", 18.0))
    balance_basis = invoice["remaining_amount"]
    interest_amount = round(balance_basis * (annual_rate / 100.0) / 365.0 * days_elapsed, 2)
    if interest_amount <= 0:
        return None

    now = datetime.now(timezone.utc)
    seq = get_next_sequence(db, "interest_charge_id")
    entry = {
        "charge_id": f"INT{seq:05d}",
        "invoice_id": invoice["invoice_id"],
        "buyer_id": invoice["buyer_id"],
        "buyer_name": invoice["buyer_name"],
        "calculation_date": now,
        "days_basis": days_elapsed,
        "balance_basis": balance_basis,
        "annual_rate_percent": annual_rate,
        "interest_amount": interest_amount,
        "status": "Applied",
        "waived_amount": 0.0,
        "waived_at": None,
        "note": None,
        "waived_by": None,
        "created_at": now,
    }
    db.interest_charges.insert_one(entry)
    # Common Accounting module: auto-post Dr Accounts Receivable / Cr Interest Income.
    ledger_service.post_interest_charge(db, entry)

    new_total = round(invoice["total_amount"] + interest_amount, 2)
    new_remaining = round(invoice["remaining_amount"] + interest_amount, 2)
    new_interest_charged = round(invoice.get("interest_charged", 0.0) + interest_amount, 2)

    db.buyer_invoices.update_one(
        {"_id": invoice["_id"]},
        {
            "$set": {
                "total_amount": new_total,
                "remaining_amount": new_remaining,
                "interest_charged": new_interest_charged,
                "last_interest_calc_date": now,
                "updated_at": now,
            }
        },
    )
    invoice["total_amount"] = new_total
    invoice["remaining_amount"] = new_remaining
    invoice["interest_charged"] = new_interest_charged
    invoice["last_interest_calc_date"] = now

    return entry


def waive_interest_charge(db, charge_id, waive_amount, note, actor=None):
    """
    Waives (fully or partially) one previously-applied interest charge and
    reduces the parent invoice's total/remaining balance to match. Admins
    use this for goodwill waivers or to correct a charge applied in error.
    """
    charge = db.interest_charges.find_one({"charge_id": charge_id})
    if not charge:
        return False, "Interest charge entry not found."

    net_outstanding = round(charge["interest_amount"] - charge.get("waived_amount", 0.0), 2)
    if waive_amount is None or waive_amount <= 0:
        return False, "Waive amount must be greater than 0."
    if waive_amount > net_outstanding + 0.01:
        return False, f"Cannot waive more than the outstanding interest of \u20b9{net_outstanding:.2f} on this charge."

    now = datetime.now(timezone.utc)
    new_waived_total = round(charge.get("waived_amount", 0.0) + waive_amount, 2)
    new_status = "Waived" if new_waived_total >= charge["interest_amount"] - 0.01 else "Adjusted"

    db.interest_charges.update_one(
        {"_id": charge["_id"]},
        {
            "$set": {
                "waived_amount": new_waived_total,
                "status": new_status,
                "waived_at": now,
                "note": note,
                "waived_by": actor,
            }
        },
    )
    # Common Accounting module: auto-post Dr Interest Income / Cr Accounts Receivable.
    ledger_service.post_interest_waiver(db, charge, waive_amount, note)

    invoice = db.buyer_invoices.find_one({"invoice_id": charge["invoice_id"]})
    if invoice:
        new_total = max(round(invoice["total_amount"] - waive_amount, 2), 0)
        new_remaining = max(round(invoice["remaining_amount"] - waive_amount, 2), 0)
        new_interest_waived = round(invoice.get("interest_waived", 0.0) + waive_amount, 2)
        status = compute_invoice_status(new_remaining, invoice["amount_paid"], invoice["due_date"], now)
        db.buyer_invoices.update_one(
            {"_id": invoice["_id"]},
            {
                "$set": {
                    "total_amount": new_total,
                    "remaining_amount": new_remaining,
                    "interest_waived": new_interest_waived,
                    "status": status,
                    "updated_at": now,
                }
            },
        )
    return True, None


def interest_history(db, invoice_id):
    return list(db.interest_charges.find({"invoice_id": invoice_id}).sort("calculation_date", -1))


# ---------------------------------------------------------------------------
# The full cycle: overdue -> reminder -> grace period -> interest
# ---------------------------------------------------------------------------


def run_dunning_cycle(db, dairy_info):
    """
    Runs the complete dunning process across every buyer invoice that
    still has money owed on it. Safe to run as often as you like -
    reminders are sent at most once per invoice automatically, and
    interest only accrues for newly-elapsed days.
    """
    settings = finance_settings_service.get_settings(db)
    now = datetime.now(timezone.utc)
    today = now.date()

    candidates = list(
        db.buyer_invoices.find({"remaining_amount": {"$gt": 0}, "status": {"$ne": "Paid"}})
    )

    stats = {
        "checked": len(candidates),
        "marked_overdue": 0,
        "reminders_sent": 0,
        "reminders_skipped": 0,
        "interest_applied": 0,
        "interest_total": 0.0,
    }

    for invoice in candidates:
        due_date = invoice["due_date"]
        if today <= due_date.date():
            continue  # not yet due - nothing to do for this invoice

        overdue_days = (today - due_date.date()).days
        update_fields = {"overdue_days": overdue_days, "updated_at": now}
        if invoice["status"] != "Overdue":
            update_fields["status"] = "Overdue"
            stats["marked_overdue"] += 1
        db.buyer_invoices.update_one({"_id": invoice["_id"]}, {"$set": update_fields})
        invoice["overdue_days"] = overdue_days
        invoice["status"] = "Overdue"

        already_reminded = db.dunning_records.find_one(
            {"invoice_id": invoice["invoice_id"], "record_type": REMINDER}
        )
        if not already_reminded:
            result = send_dunning_reminder(db, invoice, dairy_info, overdue_days, manual=False)
            if result == "sent":
                stats["reminders_sent"] += 1
            else:
                stats["reminders_skipped"] += 1

        entry = calculate_and_apply_interest(db, invoice, settings, today)
        if entry:
            stats["interest_applied"] += 1
            stats["interest_total"] = round(stats["interest_total"] + entry["interest_amount"], 2)

    return stats
