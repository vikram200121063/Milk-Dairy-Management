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
from app.services import buyer_invoice_service, invoice_service, dunning_service, finance_settings_service

buyer_invoices_bp = Blueprint("buyer_invoices", __name__, url_prefix="/buyer-invoices")

PAYMENT_METHODS = ("Cash", "UPI", "Bank Transfer", "Cheque", "NEFT/RTGS")
STATUSES = ("Unpaid", "Partially Paid", "Paid", "Overdue")


def _dairy_info():
    return {
        "name": current_app.config.get("DAIRY_NAME"),
        "address": current_app.config.get("DAIRY_ADDRESS"),
        "contact": current_app.config.get("DAIRY_CONTACT"),
    }


@buyer_invoices_bp.route("/")
@login_required
def list_invoices():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    period_number = request.args.get("period", type=int)
    status_filter = request.args.get("status", "")
    buyer_id = request.args.get("buyer_id", "")

    if not (year and month and period_number in (1, 2)):
        year, month, period_number = buyer_invoice_service.current_period_defaults()

    period = buyer_invoice_service.make_billing_period(year, month, period_number)

    mongo_filter = {"billing_period": period}
    if status_filter in STATUSES:
        mongo_filter["status"] = status_filter
    if buyer_id:
        mongo_filter["buyer_id"] = buyer_id

    invoices = list(current_app.db.buyer_invoices.find(mongo_filter).sort("buyer_name", 1))

    totals = {
        "total": sum(i["total_amount"] for i in invoices),
        "paid": sum(i["amount_paid"] for i in invoices),
        "remaining": sum(i["remaining_amount"] for i in invoices),
        "overdue_count": sum(1 for i in invoices if i["status"] == "Overdue"),
    }

    buyers = list(
        current_app.db.buyers.find({}, {"buyer_id": 1, "company_name": 1}).sort("company_name", 1)
    )

    return render_template(
        "buyer_invoices/list.html",
        invoices=invoices,
        year=year,
        month=month,
        period_number=period_number,
        period=period,
        totals=totals,
        status_filter=status_filter,
        buyer_id=buyer_id,
        buyers=buyers,
        statuses=STATUSES,
    )


@buyer_invoices_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    year = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    period_number = request.form.get("period", type=int)

    if not year or not month or period_number not in (1, 2):
        flash("Invalid billing period.", "danger")
        return redirect(url_for("buyer_invoices.list_invoices"))

    count = buyer_invoice_service.generate_invoices_for_period(
        current_app.db, year, month, period_number
    )
    flash(f"Generated/updated {count} buyer invoice(s) for this period.", "success")
    return redirect(
        url_for("buyer_invoices.list_invoices", year=year, month=month, period=period_number)
    )


@buyer_invoices_bp.route("/<invoice_id>")
@login_required
def detail(invoice_id):
    invoice = current_app.db.buyer_invoices.find_one({"invoice_id": invoice_id})
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("buyer_invoices.list_invoices"))

    sales = buyer_invoice_service.sales_for_invoice(current_app.db, invoice)
    payments = buyer_invoice_service.payments_for_invoice(current_app.db, invoice_id)
    dunning_log = dunning_service.dunning_history(current_app.db, invoice_id)
    interest_log = dunning_service.interest_history(current_app.db, invoice_id)
    settings = finance_settings_service.get_settings(current_app.db)

    return render_template(
        "buyer_invoices/detail.html",
        invoice=invoice,
        sales=sales,
        payments=payments,
        dunning_log=dunning_log,
        interest_log=interest_log,
        payment_methods=PAYMENT_METHODS,
        today=date_cls.today().isoformat(),
        settings=settings,
    )


@buyer_invoices_bp.route("/<invoice_id>/pay", methods=["POST"])
@login_required
def pay(invoice_id):
    try:
        amount = float(request.form.get("amount", "0"))
    except ValueError:
        flash("Amount must be a number.", "danger")
        return redirect(url_for("buyer_invoices.detail", invoice_id=invoice_id))

    method = request.form.get("payment_method", "").strip()
    reference = request.form.get("transaction_reference", "").strip()
    payment_date_str = request.form.get("payment_date", "").strip()

    if method not in PAYMENT_METHODS:
        flash("Please select a valid payment method.", "danger")
        return redirect(url_for("buyer_invoices.detail", invoice_id=invoice_id))

    try:
        payment_date = datetime.strptime(payment_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        payment_date = datetime.now(timezone.utc)

    ok, result = buyer_invoice_service.record_buyer_payment(
        current_app.db, invoice_id, amount, method, reference, payment_date
    )
    if not ok:
        flash(result, "danger")
    else:
        flash(f"Payment of \u20b9{amount:.2f} recorded ({result}).", "success")
    return redirect(url_for("buyer_invoices.detail", invoice_id=invoice_id))


# ---------------------------------------------------------------------------
# Printable invoice / PDF
# ---------------------------------------------------------------------------


@buyer_invoices_bp.route("/<invoice_id>/invoice")
@login_required
def invoice_view(invoice_id):
    invoice = current_app.db.buyer_invoices.find_one({"invoice_id": invoice_id})
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("buyer_invoices.list_invoices"))

    buyer = current_app.db.buyers.find_one({"buyer_id": invoice["buyer_id"]})
    sales = buyer_invoice_service.sales_for_invoice(current_app.db, invoice)

    return render_template(
        "buyer_invoices/invoice.html",
        invoice=invoice,
        buyer=buyer,
        sales=sales,
        dairy=_dairy_info(),
        back_url=url_for("buyer_invoices.detail", invoice_id=invoice_id),
        pdf_url=url_for("buyer_invoices.invoice_pdf", invoice_id=invoice_id),
    )


@buyer_invoices_bp.route("/<invoice_id>/invoice/pdf")
@login_required
def invoice_pdf(invoice_id):
    invoice = current_app.db.buyer_invoices.find_one({"invoice_id": invoice_id})
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("buyer_invoices.list_invoices"))

    buyer = current_app.db.buyers.find_one({"buyer_id": invoice["buyer_id"]})
    sales = buyer_invoice_service.sales_for_invoice(current_app.db, invoice)

    pdf_buffer = invoice_service.generate_buyer_invoice_pdf(_dairy_info(), invoice, buyer, sales)
    filename = f"invoice_{invoice['invoice_id']}.pdf"
    return send_file(
        pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=filename
    )


@buyer_invoices_bp.route("/<invoice_id>/notify", methods=["POST"])
@login_required
def notify(invoice_id):
    invoice = current_app.db.buyer_invoices.find_one({"invoice_id": invoice_id})
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("buyer_invoices.list_invoices"))

    status = dunning_service.notify_buyer_invoice(current_app.db, invoice, _dairy_info())
    category = "success" if status == "sent" else "warning"
    flash(f"{invoice['buyer_name']}: invoice email {status}.", category)
    return redirect(request.referrer or url_for("buyer_invoices.detail", invoice_id=invoice_id))


# ---------------------------------------------------------------------------
# Dunning & interest
# ---------------------------------------------------------------------------


@buyer_invoices_bp.route("/<invoice_id>/send-reminder", methods=["POST"])
@login_required
def send_reminder(invoice_id):
    invoice = current_app.db.buyer_invoices.find_one({"invoice_id": invoice_id})
    if not invoice:
        flash("Invoice not found.", "danger")
        return redirect(url_for("buyer_invoices.list_invoices"))

    today = date_cls.today()
    overdue_days = max((today - invoice["due_date"].date()).days, 0)
    status = dunning_service.send_dunning_reminder(
        current_app.db, invoice, _dairy_info(), overdue_days, manual=True
    )
    category = "success" if status == "sent" else "warning"
    flash(f"Reminder to {invoice['buyer_name']}: {status}.", category)
    return redirect(url_for("buyer_invoices.detail", invoice_id=invoice_id))


@buyer_invoices_bp.route("/run-dunning", methods=["POST"])
@login_required
def run_dunning():
    stats = dunning_service.run_dunning_cycle(current_app.db, _dairy_info())
    flash(
        f"Dunning check complete \u2013 checked {stats['checked']}, "
        f"newly overdue: {stats['marked_overdue']}, reminders sent: {stats['reminders_sent']}, "
        f"interest applied to {stats['interest_applied']} invoice(s) "
        f"(\u20b9{stats['interest_total']:.2f} total).",
        "info",
    )
    return redirect(request.referrer or url_for("buyer_invoices.list_invoices"))


@buyer_invoices_bp.route("/interest/<charge_id>/waive", methods=["POST"])
@login_required
def waive_interest(charge_id):
    charge = current_app.db.interest_charges.find_one({"charge_id": charge_id})
    if not charge:
        flash("Interest charge not found.", "danger")
        return redirect(url_for("buyer_invoices.list_invoices"))

    try:
        waive_amount = float(request.form.get("waive_amount", "0"))
    except ValueError:
        flash("Waive amount must be a number.", "danger")
        return redirect(url_for("buyer_invoices.detail", invoice_id=charge["invoice_id"]))

    note = request.form.get("note", "").strip()

    ok, error = dunning_service.waive_interest_charge(
        current_app.db, charge_id, waive_amount, note or None
    )
    if not ok:
        flash(error, "danger")
    else:
        flash(f"Waived \u20b9{waive_amount:.2f} of interest charge {charge_id}.", "success")
    return redirect(url_for("buyer_invoices.detail", invoice_id=charge["invoice_id"]))


@buyer_invoices_bp.route("/dunning-log")
@login_required
def dunning_log():
    """A flat, all-invoices view of every reminder/invoice email ever sent."""
    records = list(current_app.db.dunning_records.find().sort("sent_at", -1).limit(300))
    return render_template("buyer_invoices/dunning_log.html", records=records)
