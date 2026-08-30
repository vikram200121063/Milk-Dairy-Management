from datetime import datetime, timezone, date as date_cls

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
    send_file,
)

from app.utils.decorators import login_required
from app.services import payment_service, invoice_service, notification_service

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")

PAYMENT_METHODS = ("Cash", "UPI", "Bank Transfer")


def _current_cycle_defaults():
    today = date_cls.today()
    if today.day <= 10:
        cycle = 1
    elif today.day <= 20:
        cycle = 2
    else:
        cycle = 3
    return today.year, today.month, cycle


@payments_bp.route("/")
@login_required
def list_payments():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    cycle = request.args.get("cycle", type=int)

    if not (year and month and cycle in (1, 2, 3)):
        year, month, cycle = _current_cycle_defaults()

    period = payment_service.make_payment_period(year, month, cycle)
    payments = list(
        current_app.db.payments.find({"payment_period": period}).sort("customer_name", 1)
    )

    totals = {
        "gross": sum(p["gross_amount"] for p in payments),
        "final": sum(p["final_payable_amount"] for p in payments),
        "paid": sum(p["amount_paid"] for p in payments),
        "remaining": sum(p["remaining_amount"] for p in payments),
    }

    return render_template(
        "payments/list.html",
        payments=payments,
        year=year,
        month=month,
        cycle=cycle,
        period=period,
        totals=totals,
    )


@payments_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    year = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    cycle = request.form.get("cycle", type=int)

    if not year or not month or cycle not in (1, 2, 3):
        flash("Invalid payment period.", "danger")
        return redirect(url_for("payments.list_payments"))

    count = payment_service.generate_payments_for_cycle(current_app.db, year, month, cycle)
    flash(f"Generated/updated {count} payment record(s) for this cycle.", "success")
    return redirect(url_for("payments.list_payments", year=year, month=month, cycle=cycle))


@payments_bp.route("/<payment_id>")
@login_required
def detail(payment_id):
    payment = current_app.db.payments.find_one({"payment_id": payment_id})
    if not payment:
        flash("Payment record not found.", "danger")
        return redirect(url_for("payments.list_payments"))

    entries = list(
        current_app.db.milk_entries.find(
            {
                "customer_id": payment["customer_id"],
                "date": {"$gte": payment["cycle_start"], "$lte": payment["cycle_end"]},
            }
        ).sort("date", 1)
    )

    return render_template(
        "payments/detail.html",
        payment=payment,
        entries=entries,
        payment_methods=PAYMENT_METHODS,
        today=date_cls.today().isoformat(),
    )


@payments_bp.route("/<payment_id>/deductions", methods=["POST"])
@login_required
def set_deductions(payment_id):
    try:
        deductions = float(request.form.get("deductions", "0"))
    except ValueError:
        flash("Deductions must be a number.", "danger")
        return redirect(url_for("payments.detail", payment_id=payment_id))

    if deductions < 0:
        flash("Deductions cannot be negative.", "danger")
        return redirect(url_for("payments.detail", payment_id=payment_id))

    ok, error = payment_service.update_deductions(current_app.db, payment_id, deductions)
    if error:
        flash(error, "danger")
    else:
        flash("Deductions updated.", "success")
    return redirect(url_for("payments.detail", payment_id=payment_id))


@payments_bp.route("/<payment_id>/pay", methods=["POST"])
@login_required
def pay(payment_id):
    try:
        amount = float(request.form.get("amount", "0"))
    except ValueError:
        flash("Amount must be a number.", "danger")
        return redirect(url_for("payments.detail", payment_id=payment_id))

    method = request.form.get("payment_method", "").strip()
    reference = request.form.get("transaction_reference", "").strip()
    payment_date_str = request.form.get("payment_date", "").strip()

    if method not in PAYMENT_METHODS:
        flash("Please select a valid payment method.", "danger")
        return redirect(url_for("payments.detail", payment_id=payment_id))

    try:
        payment_date = datetime.strptime(payment_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        payment_date = datetime.now(timezone.utc)

    ok, error = payment_service.record_payment(
        current_app.db, payment_id, amount, method, reference, payment_date
    )
    if error:
        flash(error, "danger")
    else:
        flash(f"Payment of \u20b9{amount:.2f} recorded.", "success")
    return redirect(url_for("payments.detail", payment_id=payment_id))


def _load_invoice_context(payment_id):
    """
    Shared setup for both the printable HTML invoice and the PDF version:
    loads the payment, assigns/reuses its invoice number, looks up the
    customer's mobile number, and gathers the cycle's milk entries.
    Returns None if the payment doesn't exist.
    """
    payment = current_app.db.payments.find_one({"payment_id": payment_id})
    if not payment:
        return None

    invoice_number, invoice_date = invoice_service.get_or_create_invoice_number(
        current_app.db, payment
    )
    payment["invoice_number"] = invoice_number
    payment["invoice_date"] = invoice_date

    customer = current_app.db.customers.find_one({"customer_id": payment["customer_id"]})
    payment["customer_mobile"] = customer["mobile_number"] if customer else "-"

    entries = list(
        current_app.db.milk_entries.find(
            {
                "customer_id": payment["customer_id"],
                "date": {"$gte": payment["cycle_start"], "$lte": payment["cycle_end"]},
            }
        ).sort("date", 1)
    )

    return payment, entries


def _dairy_info():
    return {
        "name": current_app.config.get("DAIRY_NAME"),
        "address": current_app.config.get("DAIRY_ADDRESS"),
        "contact": current_app.config.get("DAIRY_CONTACT"),
    }


@payments_bp.route("/<payment_id>/invoice")
@login_required
def invoice(payment_id):
    context = _load_invoice_context(payment_id)
    if not context:
        flash("Payment record not found.", "danger")
        return redirect(url_for("payments.list_payments"))
    payment, entries = context

    return render_template(
        "payments/invoice.html",
        payment=payment,
        entries=entries,
        dairy=_dairy_info(),
        back_url=url_for("payments.detail", payment_id=payment_id),
        pdf_url=url_for("payments.invoice_pdf", payment_id=payment_id),
    )


@payments_bp.route("/<payment_id>/invoice/pdf")
@login_required
def invoice_pdf(payment_id):
    context = _load_invoice_context(payment_id)
    if not context:
        flash("Payment record not found.", "danger")
        return redirect(url_for("payments.list_payments"))
    payment, entries = context

    pdf_buffer = invoice_service.generate_invoice_pdf(_dairy_info(), payment, entries)
    filename = f"invoice_{payment['invoice_number']}.pdf"
    return send_file(
        pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=filename
    )


# ---------------------------------------------------------------------------
# Email notifications
# ---------------------------------------------------------------------------


def _summarize_notify_result(result):
    """Turns {"email": "sent"/error} into one short line."""
    status = result.get("email")
    if status == "sent":
        return "Email sent."
    if status:
        return f"Email: {status}"
    return "Nothing to send."


@payments_bp.route("/<payment_id>/notify", methods=["POST"])
@login_required
def notify(payment_id):
    """Sends the invoice email for one payment record, on demand."""
    context = _load_invoice_context(payment_id)  # also assigns the invoice number
    if not context:
        flash("Payment record not found.", "danger")
        return redirect(url_for("payments.list_payments"))
    payment, _entries = context

    result = notification_service.notify_invoice(current_app.db, payment, _dairy_info())
    category = "success" if result.get("email") == "sent" else "warning"
    flash(
        f"{payment['customer_name']} ({payment['payment_period']}): "
        f"{_summarize_notify_result(result)}",
        category,
    )
    return redirect(request.referrer or url_for("payments.detail", payment_id=payment_id))


@payments_bp.route("/notify-cycle", methods=["POST"])
@login_required
def notify_cycle():
    """
    Sends the invoice email to EVERY customer with a payment record in
    the given cycle - the "Send All Invoices" button on the payments
    list page. Safe to click again: already-sent invoices just get resent.
    """
    year = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    cycle = request.form.get("cycle", type=int)

    if not year or not month or cycle not in (1, 2, 3):
        flash("Invalid payment period.", "danger")
        return redirect(url_for("payments.list_payments"))

    period = payment_service.make_payment_period(year, month, cycle)
    records = list(current_app.db.payments.find({"payment_period": period}))

    if not records:
        flash(f"No payment records found for {period}. Generate payments first.", "warning")
        return redirect(url_for("payments.list_payments", year=year, month=month, cycle=cycle))

    dairy_info = _dairy_info()
    email_sent = email_failed = 0

    for payment in records:
        invoice_number, invoice_date = invoice_service.get_or_create_invoice_number(
            current_app.db, payment
        )
        payment["invoice_number"] = invoice_number
        payment["invoice_date"] = invoice_date

        result = notification_service.notify_invoice(current_app.db, payment, dairy_info)
        if result["email"] == "sent":
            email_sent += 1
        elif result["email"] not in (None, notification_service.NO_EMAIL_ON_FILE):
            email_failed += 1

    flash(
        f"Notified {len(records)} customer(s) for {period} \u2013 "
        f"Email: {email_sent} sent / {email_failed} failed.",
        "info",
    )
    return redirect(url_for("payments.list_payments", year=year, month=month, cycle=cycle))
