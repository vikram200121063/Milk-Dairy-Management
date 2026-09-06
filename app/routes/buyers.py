import re
from datetime import datetime, timezone

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)

from app.utils.decorators import login_required
from app.utils.id_generator import get_next_sequence
from app.routes.customers import MOBILE_RE, EMAIL_RE
from app.services import finance_settings_service, ledger_service

buyers_bp = Blueprint("buyers", __name__, url_prefix="/buyers")

PAYMENT_TERMS_CHOICES = (7, 15, 30, 45, 60)


def _read_and_validate_form(form, settings):
    data = {
        "company_name": form.get("company_name", "").strip(),
        "contact_person": form.get("contact_person", "").strip(),
        "mobile_number": form.get("mobile_number", "").strip(),
        "email": form.get("email", "").strip(),
        "address": form.get("address", "").strip(),
        "gst_number": form.get("gst_number", "").strip().upper(),
        "payment_terms_days_raw": form.get("payment_terms_days", "").strip(),
        "credit_limit_raw": form.get("credit_limit", "").strip(),
    }

    errors = []
    if not data["company_name"]:
        errors.append("Company / buyer name is required.")
    if not data["contact_person"]:
        errors.append("Contact person is required.")
    if not MOBILE_RE.match(data["mobile_number"]):
        errors.append("Enter a valid 10-digit mobile number.")
    if not data["email"]:
        errors.append("Email is required (used for invoices and payment reminders).")
    elif not EMAIL_RE.match(data["email"]):
        errors.append("Enter a valid email address.")
    if not data["address"]:
        errors.append("Address is required.")

    try:
        payment_terms_days = int(data["payment_terms_days_raw"]) if data["payment_terms_days_raw"] else settings["default_payment_terms_days"]
        if payment_terms_days <= 0:
            errors.append("Payment terms must be a positive number of days.")
    except ValueError:
        errors.append("Payment terms must be a whole number of days.")
        payment_terms_days = None

    try:
        credit_limit = float(data["credit_limit_raw"]) if data["credit_limit_raw"] else 0.0
        if credit_limit < 0:
            errors.append("Credit limit cannot be negative.")
    except ValueError:
        errors.append("Credit limit must be a number.")
        credit_limit = None

    data["payment_terms_days"] = payment_terms_days
    data["credit_limit"] = credit_limit
    return errors, data


@buyers_bp.route("/")
@login_required
def list_buyers():
    query_text = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "")

    mongo_filter = {}
    if query_text:
        pattern = re.escape(query_text)
        mongo_filter["$or"] = [
            {"company_name": {"$regex": pattern, "$options": "i"}},
            {"contact_person": {"$regex": pattern, "$options": "i"}},
            {"mobile_number": {"$regex": pattern}},
            {"buyer_id": {"$regex": pattern, "$options": "i"}},
        ]
    if status_filter in ("Active", "Inactive"):
        mongo_filter["status"] = status_filter

    buyers = list(current_app.db.buyers.find(mongo_filter).sort("company_name", 1))

    # Current outstanding balance per buyer, so the list gives an
    # at-a-glance view of who owes money without opening each profile.
    outstanding_by_buyer = {}
    if buyers:
        pipeline = [
            {"$match": {"remaining_amount": {"$gt": 0}}},
            {"$group": {"_id": "$buyer_id", "total": {"$sum": "$remaining_amount"}, "overdue_count": {
                "$sum": {"$cond": [{"$eq": ["$status", "Overdue"]}, 1, 0]}
            }}},
        ]
        for row in current_app.db.buyer_invoices.aggregate(pipeline):
            outstanding_by_buyer[row["_id"]] = row

    return render_template(
        "buyers/list.html",
        buyers=buyers,
        outstanding_by_buyer=outstanding_by_buyer,
        query_text=query_text,
        status_filter=status_filter,
    )


@buyers_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_buyer():
    settings = finance_settings_service.get_settings(current_app.db)

    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form, settings)

        if data["gst_number"] and current_app.db.buyers.find_one({"gst_number": data["gst_number"]}):
            errors.append("A buyer with this GST number already exists.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "buyers/form.html", buyer=data, mode="add",
                payment_terms_choices=PAYMENT_TERMS_CHOICES, settings=settings,
            )

        seq = get_next_sequence(current_app.db, "buyer_id")
        buyer_id = f"BYR{seq:04d}"

        current_app.db.buyers.insert_one(
            {
                "buyer_id": buyer_id,
                "company_name": data["company_name"],
                "contact_person": data["contact_person"],
                "mobile_number": data["mobile_number"],
                "email": data["email"],
                "address": data["address"],
                "gst_number": data["gst_number"] or None,
                "payment_terms_days": data["payment_terms_days"],
                "credit_limit": data["credit_limit"],
                "status": "Active",
                "created_at": datetime.now(timezone.utc),
            }
        )
        flash(f"Buyer {buyer_id} ({data['company_name']}) added successfully.", "success")
        return redirect(url_for("buyers.list_buyers"))

    return render_template(
        "buyers/form.html", buyer={}, mode="add",
        payment_terms_choices=PAYMENT_TERMS_CHOICES, settings=settings,
    )


@buyers_bp.route("/edit/<buyer_id>", methods=["GET", "POST"])
@login_required
def edit_buyer(buyer_id):
    buyer = current_app.db.buyers.find_one({"buyer_id": buyer_id})
    if not buyer:
        flash("Buyer not found.", "danger")
        return redirect(url_for("buyers.list_buyers"))

    settings = finance_settings_service.get_settings(current_app.db)

    if request.method == "POST":
        errors, data = _read_and_validate_form(request.form, settings)

        if data["gst_number"]:
            duplicate = current_app.db.buyers.find_one(
                {"gst_number": data["gst_number"], "buyer_id": {"$ne": buyer_id}}
            )
            if duplicate:
                errors.append("Another buyer already uses this GST number.")

        if errors:
            for error in errors:
                flash(error, "danger")
            merged = {**buyer, **data}
            return render_template(
                "buyers/form.html", buyer=merged, mode="edit",
                payment_terms_choices=PAYMENT_TERMS_CHOICES, settings=settings,
            )

        current_app.db.buyers.update_one(
            {"buyer_id": buyer_id},
            {
                "$set": {
                    "company_name": data["company_name"],
                    "contact_person": data["contact_person"],
                    "mobile_number": data["mobile_number"],
                    "email": data["email"],
                    "address": data["address"],
                    "gst_number": data["gst_number"] or None,
                    "payment_terms_days": data["payment_terms_days"],
                    "credit_limit": data["credit_limit"],
                }
            },
        )
        flash(f"Buyer {buyer_id} updated successfully.", "success")
        return redirect(url_for("buyers.profile", buyer_id=buyer_id))

    return render_template(
        "buyers/form.html", buyer=buyer, mode="edit",
        payment_terms_choices=PAYMENT_TERMS_CHOICES, settings=settings,
    )


@buyers_bp.route("/<buyer_id>")
@login_required
def profile(buyer_id):
    buyer = current_app.db.buyers.find_one({"buyer_id": buyer_id})
    if not buyer:
        flash("Buyer not found.", "danger")
        return redirect(url_for("buyers.list_buyers"))

    ledger = ledger_service.get_entity_ledger(current_app.db, "buyer", buyer_id)

    return render_template(
        "buyers/profile.html",
        buyer=buyer,
        ledger=ledger,
        detail_url=url_for("accounting.receivable_detail", buyer_id=buyer_id),
    )


@buyers_bp.route("/deactivate/<buyer_id>", methods=["POST"])
@login_required
def deactivate(buyer_id):
    current_app.db.buyers.update_one({"buyer_id": buyer_id}, {"$set": {"status": "Inactive"}})
    flash(f"Buyer {buyer_id} marked inactive.", "info")
    return redirect(request.referrer or url_for("buyers.list_buyers"))


@buyers_bp.route("/activate/<buyer_id>", methods=["POST"])
@login_required
def activate(buyer_id):
    current_app.db.buyers.update_one({"buyer_id": buyer_id}, {"$set": {"status": "Active"}})
    flash(f"Buyer {buyer_id} reactivated.", "info")
    return redirect(request.referrer or url_for("buyers.list_buyers"))
