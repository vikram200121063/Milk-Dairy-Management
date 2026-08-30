"""
Sends email notifications for milk entries and invoices, and writes a
simple audit log to the `notifications` collection so the admin can
always see what was sent, to whom, and whether it succeeded (instead of
notifications being a silent fire-and-forget action).

SMS support was removed to avoid per-message SMS costs - everything goes
out over email only. If you want SMS back later, the same pattern used
here (a *_status = _send_email(...) call + a matching log entry) can be
re-added for a new channel.
"""
from datetime import datetime, timezone

from app.services import email_service

NO_EMAIL_ON_FILE = "No email on file for this customer."


def _log(db, **fields):
    fields["sent_at"] = datetime.now(timezone.utc)
    db.notifications.insert_one(fields)


def _send_email(db, notif_type, customer, reference_id, build_fn):
    """
    Shared email-sending + logging logic. build_fn is called with no args
    and should perform the actual send (raising EmailSendError on
    failure). Returns a short human-readable status string.
    """
    if not customer.get("email"):
        return NO_EMAIL_ON_FILE
    if not email_service.email_configured():
        return "Email not configured."

    try:
        build_fn()
        status_text = "sent"
        status, error = "sent", None
    except email_service.EmailSendError as exc:
        status_text = str(exc)
        status, error = "failed", status_text

    _log(
        db,
        type=notif_type,
        channel="email",
        customer_id=customer["customer_id"],
        customer_name=customer["name"],
        reference_id=str(reference_id),
        status=status,
        error=error,
    )
    return status_text


def notify_milk_entry(db, entry):
    """
    Sends the milk-entry email for one entry to its customer.
    Returns {"email": "..."} with a short status/error string (the
    caller decides how to summarize/flash this).
    """
    customer = db.customers.find_one({"customer_id": entry["customer_id"]})
    if not customer:
        return {"email": "Customer not found."}

    email_status = _send_email(
        db, "milk_entry", customer, entry["_id"],
        lambda: email_service.send_milk_entry_email(customer, entry),
    )
    return {"email": email_status}


def notify_invoice(db, payment, dairy_info):
    """
    Sends the invoice email (PDF attached) for one payment record.
    Assumes `payment` already has invoice_number/invoice_date set - call
    invoice_service.get_or_create_invoice_number(db, payment) first.
    """
    customer = db.customers.find_one({"customer_id": payment["customer_id"]})
    if not customer:
        return {"email": "Customer not found."}

    entries = list(
        db.milk_entries.find(
            {
                "customer_id": payment["customer_id"],
                "date": {"$gte": payment["cycle_start"], "$lte": payment["cycle_end"]},
            }
        ).sort("date", 1)
    )
    email_status = _send_email(
        db, "invoice", customer, payment["payment_id"],
        lambda: email_service.send_invoice_email(customer, payment, entries, dairy_info),
    )
    return {"email": email_status}
