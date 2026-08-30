from datetime import datetime, timezone

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    current_app,
    send_file,
)
from werkzeug.security import generate_password_hash, check_password_hash

from app.utils.decorators import customer_login_required
from app.routes.customers import MOBILE_RE
from app.routes.payments import _current_cycle_defaults
from app.services import payment_service, invoice_service

customer_portal_bp = Blueprint("customer_portal", __name__, url_prefix="/portal")


# ---------------------------------------------------------------------------
# Registration & login
#
# A customer must already exist in the `customers` collection (added by the
# dairy admin when they start supplying milk) before they can create a
# portal login. Registration "claims" that existing record by matching the
# mobile number on file and attaching a password_hash to it - it does NOT
# create a brand new customer record, so every portal account is always
# backed by a real, admin-verified customer.
# ---------------------------------------------------------------------------


@customer_portal_bp.route("/register", methods=["GET", "POST"])
def register():
    if "customer_id" in session:
        return redirect(url_for("customer_portal.dashboard"))

    if request.method == "POST":
        mobile_number = request.form.get("mobile_number", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if not MOBILE_RE.match(mobile_number):
            errors.append("Enter a valid 10-digit mobile number.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters long.")
        if password != confirm_password:
            errors.append("Passwords do not match.")

        customer = None
        if not errors:
            customer = current_app.db.customers.find_one({"mobile_number": mobile_number})
            if not customer:
                errors.append(
                    "No customer record found with this mobile number. "
                    "Please contact the dairy office to be registered first."
                )
            elif customer.get("password_hash"):
                errors.append(
                    "This mobile number is already registered. Please log in instead."
                )

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("portal/register.html", mobile_number=mobile_number)

        current_app.db.customers.update_one(
            {"_id": customer["_id"]},
            {
                "$set": {
                    "password_hash": generate_password_hash(password),
                    "portal_registered_at": datetime.now(timezone.utc),
                }
            },
        )
        flash("Account created! You can now log in with your mobile number.", "success")
        return redirect(url_for("customer_portal.login"))

    return render_template("portal/register.html", mobile_number="")


@customer_portal_bp.route("/login", methods=["GET", "POST"])
def login():
    if "customer_id" in session:
        return redirect(url_for("customer_portal.dashboard"))

    if request.method == "POST":
        mobile_number = request.form.get("mobile_number", "").strip()
        password = request.form.get("password", "")

        if not mobile_number or not password:
            flash("Mobile number and password are both required.", "danger")
            return render_template("portal/login.html")

        customer = current_app.db.customers.find_one({"mobile_number": mobile_number})

        # Same non-committal error for "no account" and "wrong password" so
        # we don't reveal which mobile numbers are registered.
        if (
            customer
            and customer.get("password_hash")
            and check_password_hash(customer["password_hash"], password)
        ):
            session.clear()
            session["customer_id"] = customer["customer_id"]
            session["customer_name"] = customer["name"]
            flash(f"Welcome back, {customer['name']}!", "success")

            next_page = request.args.get("next")
            return redirect(next_page or url_for("customer_portal.dashboard"))

        flash("Invalid mobile number or password.", "danger")

    return render_template("portal/login.html")


@customer_portal_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("customer_portal.login"))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _current_customer():
    """Loads the full customer document for the logged-in portal user."""
    return current_app.db.customers.find_one({"customer_id": session["customer_id"]})


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@customer_portal_bp.route("/")
@customer_login_required
def dashboard():
    customer = _current_customer()
    if not customer:
        # The underlying customer record vanished (shouldn't normally
        # happen since we never hard-delete customers) - log them out safely.
        session.clear()
        flash("Your account could not be found. Please contact the dairy office.", "danger")
        return redirect(url_for("customer_portal.login"))

    customer_id = customer["customer_id"]

    year, month, cycle = _current_cycle_defaults()
    start, end = payment_service.get_cycle_boundaries(year, month, cycle)

    current_cycle_entries = list(
        current_app.db.milk_entries.find(
            {"customer_id": customer_id, "date": {"$gte": start, "$lte": end}}
        )
    )
    current_cycle_stats = {
        "quantity": sum(e["quantity"] for e in current_cycle_entries),
        "amount": sum(e["total_amount"] for e in current_cycle_entries),
        "count": len(current_cycle_entries),
    }

    recent_entries = list(
        current_app.db.milk_entries.find({"customer_id": customer_id})
        .sort("date", -1)
        .limit(10)
    )

    pending_payments = list(
        current_app.db.payments.find(
            {"customer_id": customer_id, "remaining_amount": {"$gt": 0}}
        )
    )
    pending_total = sum(p["remaining_amount"] for p in pending_payments)

    recent_payments = list(
        current_app.db.payments.find({"customer_id": customer_id})
        .sort("cycle_start", -1)
        .limit(5)
    )

    return render_template(
        "portal/dashboard.html",
        customer=customer,
        current_cycle_stats=current_cycle_stats,
        current_period=payment_service.make_payment_period(year, month, cycle),
        recent_entries=recent_entries,
        pending_total=pending_total,
        pending_count=len(pending_payments),
        recent_payments=recent_payments,
    )


# ---------------------------------------------------------------------------
# Milk records
# ---------------------------------------------------------------------------


@customer_portal_bp.route("/milk-entries")
@customer_login_required
def milk_entries():
    customer_id = session["customer_id"]

    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    filters = {"customer_id": customer_id}
    date_filter = {}
    if date_from:
        try:
            date_filter["$gte"] = datetime.strptime(date_from, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    if date_to:
        try:
            date_filter["$lte"] = datetime.strptime(date_to, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    if date_filter:
        filters["date"] = date_filter

    entries = list(current_app.db.milk_entries.find(filters).sort("date", -1).limit(200))

    totals = {
        "quantity": sum(e["quantity"] for e in entries),
        "amount": sum(e["total_amount"] for e in entries),
        "count": len(entries),
    }

    return render_template(
        "portal/milk_entries.html",
        entries=entries,
        totals=totals,
        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------------------------
# Payments / invoices
# ---------------------------------------------------------------------------


@customer_portal_bp.route("/payments")
@customer_login_required
def payments():
    customer_id = session["customer_id"]

    records = list(
        current_app.db.payments.find({"customer_id": customer_id}).sort("cycle_start", -1)
    )

    totals = {
        "final": sum(p["final_payable_amount"] for p in records),
        "paid": sum(p["amount_paid"] for p in records),
        "remaining": sum(p["remaining_amount"] for p in records),
    }

    return render_template("portal/payments.html", payments=records, totals=totals)


@customer_portal_bp.route("/payments/<payment_id>/invoice")
@customer_login_required
def invoice(payment_id):
    payment = current_app.db.payments.find_one({"payment_id": payment_id})
    if not payment or payment["customer_id"] != session["customer_id"]:
        flash("Invoice not found.", "danger")
        return redirect(url_for("customer_portal.payments"))

    invoice_number, invoice_date = invoice_service.get_or_create_invoice_number(
        current_app.db, payment
    )
    payment["invoice_number"] = invoice_number
    payment["invoice_date"] = invoice_date

    customer = _current_customer()
    payment["customer_mobile"] = customer["mobile_number"] if customer else "-"

    entries = list(
        current_app.db.milk_entries.find(
            {
                "customer_id": payment["customer_id"],
                "date": {"$gte": payment["cycle_start"], "$lte": payment["cycle_end"]},
            }
        ).sort("date", 1)
    )

    dairy_info = {
        "name": current_app.config.get("DAIRY_NAME"),
        "address": current_app.config.get("DAIRY_ADDRESS"),
        "contact": current_app.config.get("DAIRY_CONTACT"),
    }

    return render_template(
        "payments/invoice.html",
        payment=payment,
        entries=entries,
        dairy=dairy_info,
        back_url=url_for("customer_portal.payments"),
        pdf_url=url_for("customer_portal.invoice_pdf", payment_id=payment_id),
    )


@customer_portal_bp.route("/payments/<payment_id>/invoice/pdf")
@customer_login_required
def invoice_pdf(payment_id):
    payment = current_app.db.payments.find_one({"payment_id": payment_id})
    if not payment or payment["customer_id"] != session["customer_id"]:
        flash("Invoice not found.", "danger")
        return redirect(url_for("customer_portal.payments"))

    invoice_number, invoice_date = invoice_service.get_or_create_invoice_number(
        current_app.db, payment
    )
    payment["invoice_number"] = invoice_number
    payment["invoice_date"] = invoice_date

    customer = _current_customer()
    payment["customer_mobile"] = customer["mobile_number"] if customer else "-"

    entries = list(
        current_app.db.milk_entries.find(
            {
                "customer_id": payment["customer_id"],
                "date": {"$gte": payment["cycle_start"], "$lte": payment["cycle_end"]},
            }
        ).sort("date", 1)
    )

    dairy_info = {
        "name": current_app.config.get("DAIRY_NAME"),
        "address": current_app.config.get("DAIRY_ADDRESS"),
        "contact": current_app.config.get("DAIRY_CONTACT"),
    }

    pdf_buffer = invoice_service.generate_invoice_pdf(dairy_info, payment, entries)
    filename = f"invoice_{payment['invoice_number']}.pdf"
    return send_file(
        pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=filename
    )
